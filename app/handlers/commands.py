"""Telegram command handlers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from telegram import Update
from telegram.ext import Application, CallbackContext, CommandHandler, MessageHandler, filters

from app.jobs.subscriptions import SubscriptionService
from app.planner import GeminiPlanner
from app.store.db import Database
from app.store.repository import Repository
# escape_markdown retained for future manual formatting use
from app.utils.logging import get_logger
from app.utils.rate_limit import RateLimiter
from app.utils.routers import resolve_router

logger = get_logger(__name__)


@dataclass
class HandlerContext:
    db: Database
    planner: GeminiPlanner
    rate_limiter: RateLimiter
    routers: dict[str, dict[str, str]]
    network: str
    default_lookback: int
    subscription_service: SubscriptionService
    admin_ids: list[int]


def setup(application: Application, handler_context: HandlerContext) -> None:
    """Register handlers on the Telegram application."""
    application.bot_data["ctx"] = handler_context

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("routers", routers_command))
    application.add_handler(CommandHandler("latest", latest_command))
    application.add_handler(CommandHandler("summary", summary_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("setnetwork", admin_only(set_network)))
    application.add_handler(CommandHandler("setlookback", admin_only(set_default_lookback)))
    application.add_handler(CommandHandler("setmax", admin_only(set_max_items)))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, natural_language_handler)
    )


def get_ctx(context: CallbackContext) -> HandlerContext:
    return context.application.bot_data["ctx"]


async def ensure_user(update: Update, context: CallbackContext) -> None:
    ctx = get_ctx(context)
    if update.effective_user is None:
        return
    async with ctx.db.session() as session:
        repo = Repository(session)
        await repo.get_or_create_user(update.effective_user.id)


async def start(update: Update, context: CallbackContext) -> None:
    await ensure_user(update, context)
    text = (
        "Welcome to the Base MCP bot. Ask about router activity or token summaries. "
        "Try commands like /latest uniswap_v3 15."
    )
    await update.message.reply_text(text)


async def help_command(update: Update, context: CallbackContext) -> None:
    await ensure_user(update, context)
    text = (
        "Commands:\n"
        "/latest <router> [minutes] — recent transactions\n"
        "/summary <router> [minutes] — router + Dexscreener summary\n"
        "/subscribe <router> — periodic updates\n"
        "/unsubscribe <router>\n"
        "/routers — list supported routers"
    )
    await update.message.reply_text(text)


async def routers_command(update: Update, context: CallbackContext) -> None:
    await ensure_user(update, context)
    ctx = get_ctx(context)
    routers = ", ".join(sorted(ctx.routers.keys()))
    await update.message.reply_text(f"Routers for {ctx.network}: {routers}")


async def latest_command(update: Update, context: CallbackContext) -> None:
    await ensure_user(update, context)
    ctx = get_ctx(context)
    if not rate_limit(update, context):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /latest <router> [minutes]")
        return
    router_key = args[0].lower()
    minutes = int(args[1]) if len(args) > 1 else ctx.default_lookback

    try:
        router = resolve_router(router_key, ctx.network, ctx.routers)
    except KeyError:
        await update.message.reply_text(f"Unknown router: {router_key}")
        return

    text = (
        f"Provide the latest {minutes} minute transactions for router {router_key} "
        f"at address {router.address}. Summarise key swaps."
    )
    await send_planner_response(update, context, text)


async def summary_command(update: Update, context: CallbackContext) -> None:
    await ensure_user(update, context)
    ctx = get_ctx(context)
    if not rate_limit(update, context):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /summary <router> [minutes]")
        return
    router_key = args[0].lower()
    minutes = int(args[1]) if len(args) > 1 else ctx.default_lookback

    try:
        router = resolve_router(router_key, ctx.network, ctx.routers)
    except KeyError:
        await update.message.reply_text(f"Unknown router: {router_key}")
        return

    text = (
        f"Summarise swaps on router {router_key} at {router.address} "
        f"within the last {minutes} minutes, then pull Dexscreener token data."
    )
    await send_planner_response(update, context, text)


async def subscribe_command(update: Update, context: CallbackContext) -> None:
    await ensure_user(update, context)
    ctx = get_ctx(context)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /subscribe <router> [minutes]")
        return

    router_key = args[0].lower()
    minutes = int(args[1]) if len(args) > 1 else ctx.default_lookback
    try:
        resolve_router(router_key, ctx.network, ctx.routers)
    except KeyError:
        await update.message.reply_text(f"Unknown router: {router_key}")
        return

    async with ctx.db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(update.effective_user.id)
        await repo.add_subscription(user.id, router_key, minutes)

    await update.message.reply_text(
        f"Subscribed to {router_key} updates every {ctx.subscription_service.interval_minutes} minutes."
    )


async def unsubscribe_command(update: Update, context: CallbackContext) -> None:
    await ensure_user(update, context)
    ctx = get_ctx(context)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /unsubscribe <router>")
        return
    router_key = args[0].lower()
    async with ctx.db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(update.effective_user.id)
        await repo.remove_subscription(user.id, router_key)
    await update.message.reply_text(f"Unsubscribed from {router_key}.")


async def set_network(update: Update, context: CallbackContext) -> None:
    ctx = get_ctx(context)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /setnetwork <network>")
        return

    ctx.network = args[0]
    await update.message.reply_text(f"Network set to {ctx.network}.")


async def set_default_lookback(update: Update, context: CallbackContext) -> None:
    ctx = get_ctx(context)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /setlookback <minutes>")
        return
    ctx.default_lookback = int(args[0])
    await update.message.reply_text(f"Default lookback set to {ctx.default_lookback} minutes.")


async def set_max_items(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Max items is controlled via environment settings.")


def admin_only(func):
    async def wrapper(update: Update, context: CallbackContext):
        ctx = get_ctx(context)
        user_id = update.effective_user.id if update.effective_user else None
        if user_id not in ctx.admin_ids:
            await update.message.reply_text("Admin only.")
            return
        await func(update, context)

    return wrapper


def rate_limit(update: Update, context: CallbackContext) -> bool:
    ctx = get_ctx(context)
    user = update.effective_user
    if not user:
        return True
    allowed = ctx.rate_limiter.allow(user.id)
    if not allowed:
        asyncio.create_task(
            update.message.reply_text("Slow down — hit rate limit. Try again shortly.")
        )
    return allowed


async def natural_language_handler(update: Update, context: CallbackContext) -> None:
    await ensure_user(update, context)
    if not rate_limit(update, context):
        return
    message = update.message.text
    await send_planner_response(update, context, message)


async def send_planner_response(update: Update, context: CallbackContext, message: str) -> None:
    ctx = get_ctx(context)
    payload = {
        "network": ctx.network,
        "default_lookback": ctx.default_lookback,
    }
    response = await ctx.planner.run(message, payload)
    if update.message:
        await update.message.reply_text(response, parse_mode="MarkdownV2", disable_web_page_preview=True)
