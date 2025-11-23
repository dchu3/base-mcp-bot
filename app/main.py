"""Application entrypoint."""

from __future__ import annotations

import asyncio
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder

from app.config import load_settings
from app.handlers.commands import HandlerContext, setup as setup_handlers
from app.jobs.cleanup import CleanupService
from app.mcp_client import MCPManager
from app.planner import GeminiPlanner
from app.store.db import Database
from app.store.repository import Repository
from app.utils.logging import configure_logging, get_logger
from app.utils.rate_limit import RateLimiter
from app.utils.prompts import load_prompt_template
from app.utils.routers import load_router_map

logger = get_logger(__name__)


async def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)

    db = Database(settings.database_url)
    db.connect()
    await db.init_models()

    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    await application.initialize()

    # Simplified command menu - conversational bot
    commands = [
        BotCommand("help", "Show what I can do"),
        BotCommand("history", "View recent conversation"),
        BotCommand("clear", "Clear conversation history"),
    ]

    await application.bot.delete_my_commands(scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(commands, scope=BotCommandScopeDefault())

    if settings.telegram_chat_id is not None:
        scope = BotCommandScopeChat(chat_id=settings.telegram_chat_id)
        try:
            await application.bot.delete_my_commands(scope=scope)
        except BadRequest:
            logger.warning(
                "telegram_command_scope_delete_failed",
                chat_id=settings.telegram_chat_id,
            )
        await application.bot.set_my_commands(commands, scope=scope)

    mcp_manager = MCPManager(
        base_cmd=settings.mcp_base_server_cmd,
        dexscreener_cmd=settings.mcp_dexscreener_cmd,
        honeypot_cmd=settings.mcp_honeypot_cmd,
    )
    await mcp_manager.start()

    router_map = load_router_map()
    router_keys = list(router_map.keys())

    prompt_template = load_prompt_template(settings.planner_prompt_file)
    planner = GeminiPlanner(
        api_key=settings.gemini_api_key,
        mcp_manager=mcp_manager,
        router_keys=router_keys,
        router_map=router_map,
        model_name=settings.gemini_model,
        prompt_template=prompt_template,
        confidence_threshold=settings.planner_confidence_threshold,
        enable_reflection=settings.planner_enable_reflection,
        max_iterations=settings.planner_max_iterations,
    )

    rate_limiter = RateLimiter(settings.rate_limit_per_user_per_min)
    scheduler = AsyncIOScheduler()

    # Cleanup service for old conversations and expired context
    cleanup_service = CleanupService(db=db, scheduler=scheduler)

    handler_context = HandlerContext(
        db=db,
        planner=planner,
        rate_limiter=rate_limiter,
        admin_ids=settings.admin_user_ids,
        allowed_chat_id=settings.telegram_chat_id,
    )

    if settings.telegram_chat_id is not None:
        async with db.session() as session:
            repo = Repository(session)
            await repo.get_or_create_user(settings.telegram_chat_id)

    setup_handlers(application, handler_context)

    # Start cleanup jobs
    cleanup_service.start()
    scheduler.start()

    try:
        await application.start()
        if application.updater:
            await application.updater.start_polling()

        logger.info("bot_started", commands=len(commands))

        stop_event = asyncio.Event()

        def signal_handler(signum, frame):
            logger.info("shutdown_signal_received", signal=signum)
            stop_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        await stop_event.wait()

    finally:
        logger.info("bot_stopping")
        scheduler.shutdown(wait=False)
        if application.updater:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await mcp_manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
