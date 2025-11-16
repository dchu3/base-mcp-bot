"""Application entrypoint."""

from __future__ import annotations

import asyncio
import signal
from argparse import ArgumentParser, ArgumentTypeError

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


async def main(interval_override_minutes: int | None = None) -> None:
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
        BotCommand("subscriptions", "Show your router subscriptions"),
        BotCommand("subscribe", "Subscribe to router updates"),
        BotCommand("unsubscribe", "Stop router updates"),
        BotCommand("unsubscribe_all", "Stop all router updates"),
        BotCommand("watch", "Add a token to your watchlist"),
        BotCommand("watchlist", "Show saved tokens"),
        BotCommand("unwatch", "Remove a token from the watchlist"),
        BotCommand("unwatch_all", "Clear the watchlist"),
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

    router_keys = list(routers.keys())
    prompt_template = load_prompt_template(settings.planner_prompt_file)
    planner = GeminiPlanner(
        api_key=settings.gemini_api_key,
        mcp_manager=mcp_manager,
        router_keys=router_keys,
        router_map=routers,
        model_name=settings.gemini_model,
        prompt_template=prompt_template,
        confidence_threshold=settings.planner_confidence_threshold,
        enable_reflection=settings.planner_enable_reflection,
        max_iterations=settings.planner_max_iterations,
    )
    rate_limiter = RateLimiter(settings.rate_limit_per_user_per_min)
    scheduler = AsyncIOScheduler()
    interval_minutes = (
        interval_override_minutes
        if interval_override_minutes is not None
        else settings.scheduler_interval_minutes
    )
    if interval_override_minutes is not None:
        logger.info(
            "scheduler_interval_override",
            configured=settings.scheduler_interval_minutes,
            override=interval_minutes,
        )

    subscription_service = SubscriptionService(
        scheduler=scheduler,
        db=db,
        mcp_manager=mcp_manager,
        planner=planner,
        routers=routers,
        network=settings.base_network,
        bot=application.bot,
        interval_minutes=interval_minutes,
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


def _parse_interval(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ArgumentTypeError("must be an integer") from exc
    if not 1 <= parsed <= 60:
        raise ArgumentTypeError("must be between 1 and 60 minutes")
    return parsed


def _build_arg_parser() -> ArgumentParser:
    parser = ArgumentParser(description="Base MCP bot")
    parser.add_argument(
        "--scheduler-interval-minutes",
        type=_parse_interval,
        dest="scheduler_interval_minutes",
        help="override the subscription scheduler frequency (1-60 minutes)",
    )
    return parser


if __name__ == "__main__":
    parser = _build_arg_parser()
    args = parser.parse_args()
    try:
        asyncio.run(main(args.scheduler_interval_minutes))
    except KeyboardInterrupt:
        logger.info("shutdown_requested_by_keyboard")
