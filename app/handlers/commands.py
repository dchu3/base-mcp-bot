"""Telegram command handlers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackContext, CommandHandler, MessageHandler, filters

from app.jobs.subscriptions import SubscriptionService
from app.planner import GeminiPlanner
from app.store.db import Database
from app.store.repository import Repository
from app.utils.formatting import escape_markdown
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
    allowed_chat_id: int | None


def setup(application: Application, handler_context: HandlerContext) -> None:
    """Register handlers on the Telegram application."""
    application.bot_data["ctx"] = handler_context

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("routers", routers_command))
    application.add_handler(CommandHandler("latest", latest_command))
    application.add_handler(CommandHandler("subscriptions", subscriptions_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("unsubscribe_all", unsubscribe_all_command))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, natural_language_handler)
    )


def get_ctx(context: CallbackContext) -> HandlerContext:
    return context.application.bot_data["ctx"]


async def ensure_user(update: Update, context: CallbackContext) -> bool:
    ctx = get_ctx(context)
    allowed_chat_id = ctx.allowed_chat_id
    chat = update.effective_chat
    if allowed_chat_id is not None:
        if chat is None or chat.id != allowed_chat_id:
            if update.message:
                await update.message.reply_text("This bot is restricted to the configured chat.")
            return False
    if update.effective_user is None:
        return False
    async with ctx.db.session() as session:
        repo = Repository(session)
        target_chat_id = allowed_chat_id or update.effective_user.id
        await repo.get_or_create_user(target_chat_id)
    return True


async def start(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    text = (
        "Welcome to the Base MCP bot. Ask about router activity or token summaries. "
        "Try commands like /latest uniswap_v3 15."
    )
    await update.message.reply_text(text)


async def help_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    text = (
        "Commands:\n"
        "/latest <router> [minutes] — recent transactions + Dexscreener stats\n"
        "/subscribe <router> [minutes] — periodic updates\n"
        "/unsubscribe <router> — stop updates\n"
        "/unsubscribe_all — stop all router updates\n"
        "/subscriptions — list your current router alerts\n"
        "/routers — list supported router keys\n\n"
        "You can also ask natural-language questions like "
        "'latest uniswap_v3 swaps last 15 minutes'."
    )
    await update.message.reply_text(text)


async def routers_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    ctx = get_ctx(context)
    lines = []
    for key in sorted(ctx.routers.keys()):
        try:
            info = resolve_router(key, ctx.network, ctx.routers)
            lines.append(
                f"• {escape_markdown(key)} — `{escape_markdown(info.address)}`"
            )
        except KeyError:
            lines.append(
                f"• {escape_markdown(key)} — not available on {escape_markdown(ctx.network)}"
            )
    header = escape_markdown(f"Routers for {ctx.network}:")
    message = "\n".join([header, *lines]) if lines else escape_markdown(
        f"No routers configured for {ctx.network}."
    )
    await update.message.reply_text(message, parse_mode="MarkdownV2")


async def latest_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
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
    if not await ensure_user(update, context):
        return
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
    if not await ensure_user(update, context):
        return
    ctx = get_ctx(context)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /subscribe <router> [minutes]")
        return

    router_key = args[0].lower()
    minutes = ctx.default_lookback
    if len(args) > 1:
        try:
            minutes = int(args[1])
        except ValueError:
            await update.message.reply_text("Minutes must be a whole number.")
            return

    if minutes <= 0:
        await update.message.reply_text("Minutes must be greater than zero.")
        return
    try:
        resolve_router(router_key, ctx.network, ctx.routers)
    except KeyError:
        await update.message.reply_text(f"Unknown router: {router_key}")
        return

    async with ctx.db.session() as session:
        repo = Repository(session)
        target_id = ctx.allowed_chat_id or update.effective_user.id
        user = await repo.get_or_create_user(target_id)
        await repo.add_subscription(user.id, router_key, minutes)

    await update.message.reply_text(
        f"Subscribed to {router_key} updates every {minutes} minutes."
    )


async def subscriptions_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    ctx = get_ctx(context)
    target_id = ctx.allowed_chat_id or update.effective_user.id

    async with ctx.db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(target_id)
        subscriptions = await repo.list_subscriptions(user.id)

    if not subscriptions:
        await update.message.reply_text("No active subscriptions.")
        return

    lines = []
    for subscription in sorted(subscriptions, key=lambda item: item.router_key):
        try:
            router = resolve_router(subscription.router_key, ctx.network, ctx.routers)
            address = escape_markdown(router.address)
            lines.append(
                f"• {escape_markdown(subscription.router_key)} — `{address}` every "
                f"{escape_markdown(str(subscription.lookback_minutes))} minutes"
            )
        except KeyError:
            lines.append(
                f"• {escape_markdown(subscription.router_key)} — unavailable on "
                f"{escape_markdown(ctx.network)} every "
                f"{escape_markdown(str(subscription.lookback_minutes))} minutes"
            )

    header = escape_markdown("Active subscriptions:")
    message = "\n".join([header, *lines])
    await update.message.reply_text(message, parse_mode="MarkdownV2")


async def unsubscribe_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    ctx = get_ctx(context)
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /unsubscribe <router>")
        return
    router_key = args[0].lower()
    async with ctx.db.session() as session:
        repo = Repository(session)
        target_id = ctx.allowed_chat_id or update.effective_user.id
        user = await repo.get_or_create_user(target_id)
        await repo.remove_subscription(user.id, router_key)
    await update.message.reply_text(f"Unsubscribed from {router_key}.")


async def unsubscribe_all_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    ctx = get_ctx(context)
    async with ctx.db.session() as session:
        repo = Repository(session)
        target_id = ctx.allowed_chat_id or update.effective_user.id
        user = await repo.get_or_create_user(target_id)
        await repo.remove_all_subscriptions(user.id)
    await update.message.reply_text("All subscriptions removed.")


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
        allowed_chat_id = ctx.allowed_chat_id
        chat = update.effective_chat
        if allowed_chat_id is not None:
            if chat is None or chat.id != allowed_chat_id:
                if update.message:
                    await update.message.reply_text("This bot is restricted to the configured chat.")
                return
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
    if not await ensure_user(update, context):
        return
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
    try:
        response = await ctx.planner.run(message, payload)
    except Exception as exc:
        logger.error("planner_execution_failed", error=str(exc))
        if update.message:
            safe_message = escape_markdown(f"Planner error: {exc}")
            await update.message.reply_text(
                safe_message,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True,
            )
        return
    if update.message:
        rendered = response.strip() if isinstance(response, str) else ""
        if not rendered:
            await update.message.reply_text(
                "No recent data returned for that request.",
                disable_web_page_preview=True,
            )
            return
        markdown_text = rendered
        try:
            await update.message.reply_text(
                markdown_text,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            logger.warning(
                "telegram_markdown_failed", error=str(exc), text=markdown_text
            )
            await update.message.reply_text(
                rendered,
                disable_web_page_preview=True,
            )
