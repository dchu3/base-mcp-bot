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
from app.jobs.subscriptions import SubscriptionService
from app.mcp_client import MCPManager
from app.planner import GeminiPlanner
from app.store.db import Database
from app.store.repository import Repository
from app.utils.logging import configure_logging, get_logger
from app.utils.rate_limit import RateLimiter
from app.utils.routers import load_router_map
from app.utils.prompts import load_prompt_template

logger = get_logger(__name__)


async def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)

    routers = load_router_map(settings.routers_json)
    db = Database(settings.database_url)
    db.connect()
    await db.init_models()

    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    await application.initialize()

    commands = [
        BotCommand("help", "Show available options"),
        BotCommand("routers", "List supported routers"),
        BotCommand("latest", "Latest transactions for a router"),
        BotCommand("subscribe", "Subscribe to router updates"),
        BotCommand("unsubscribe", "Stop router updates"),
    ]

    await application.bot.delete_my_commands(scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(commands, scope=BotCommandScopeDefault())

    if settings.telegram_chat_id is not None:
        scope = BotCommandScopeChat(chat_id=settings.telegram_chat_id)
        try:
            await application.bot.delete_my_commands(scope=scope)
        except BadRequest:
            logger.warning(
                "telegram_command_scope_delete_failed", chat_id=settings.telegram_chat_id
            )
        await application.bot.set_my_commands(commands, scope=scope)

    mcp_manager = MCPManager(settings.mcp_base_server_cmd, settings.mcp_dexscreener_cmd)
    await mcp_manager.start()

    router_keys = list(routers.keys())
    prompt_template = load_prompt_template(settings.planner_prompt_file)
    planner = GeminiPlanner(
        api_key=settings.gemini_api_key,
        mcp_manager=mcp_manager,
        router_keys=router_keys,
        router_map=routers,
        model_name=settings.gemini_model,
        prompt_template=prompt_template,
    )
    rate_limiter = RateLimiter(settings.rate_limit_per_user_per_min)
    scheduler = AsyncIOScheduler()
    subscription_service = SubscriptionService(
        scheduler=scheduler,
        db=db,
        mcp_manager=mcp_manager,
        routers=routers,
        network=settings.base_network,
        bot=application.bot,
        interval_minutes=settings.scheduler_interval_minutes,
        override_chat_id=settings.telegram_chat_id,
    )
    handler_context = HandlerContext(
        db=db,
        planner=planner,
        rate_limiter=rate_limiter,
        routers=routers,
        network=settings.base_network,
        default_lookback=settings.default_lookback_minutes,
        subscription_service=subscription_service,
        admin_ids=settings.admin_user_ids,
        allowed_chat_id=settings.telegram_chat_id,
    )

    if settings.telegram_chat_id is not None:
        async with db.session() as session:
            repo = Repository(session)
            await repo.get_or_create_user(settings.telegram_chat_id)
    setup_handlers(application, handler_context)

    subscription_service.start()

    try:
        await application.start()
        if application.updater:
            await application.updater.start_polling()

        stop_event = asyncio.Event()

        def _signal_handler(*_: int) -> None:
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        try:
            await stop_event.wait()
        finally:
            if application.updater:
                await application.updater.stop()
            await application.stop()
            await application.shutdown()
    finally:
        await subscription_service.shutdown()
        await mcp_manager.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("shutdown_requested_by_keyboard")
