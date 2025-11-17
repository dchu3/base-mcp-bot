"""Telegram command handlers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, List

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.jobs.subscriptions import SubscriptionService
from app.planner import GeminiPlanner
from app.store.db import Database, TokenContext, TokenWatch
from app.store.repository import Repository
from app.utils.formatting import escape_markdown, unescape_markdown
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
    application.add_handler(CommandHandler("watch", watch_command))
    application.add_handler(CommandHandler("watchlist", watchlist_command))
    application.add_handler(CommandHandler("unwatch", unwatch_command))
    application.add_handler(CommandHandler("unwatch_all", unwatch_all_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("clear", clear_command))

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
                await update.message.reply_text(
                    "This bot is restricted to the configured chat.",
                    parse_mode=None,
                )
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
    await update.message.reply_text(text, parse_mode=None)


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
        "/watch <token_address> [symbol] [label] — add/update a watchlist entry\n"
        "/watchlist — show your saved tokens\n"
        "/unwatch <token_address> — remove a single token\n"
        "/unwatch_all — clear the entire watchlist\n\n"
        "/history — view recent conversation messages\n"
        "/clear — clear conversation history and start fresh\n\n"
        "You can also ask natural-language questions like "
        "'latest uniswap_v3 swaps last 15 minutes'."
    )
    await update.message.reply_text(text, parse_mode=None)


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
    message = (
        "\n".join([header, *lines])
        if lines
        else escape_markdown(f"No routers configured for {ctx.network}.")
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
        await update.message.reply_text(
            "Usage: /latest <router> [minutes]", parse_mode=None
        )
        return
    router_key = args[0].lower()
    minutes = int(args[1]) if len(args) > 1 else ctx.default_lookback

    try:
        router = resolve_router(router_key, ctx.network, ctx.routers)
    except KeyError:
        await update.message.reply_text(
            f"Unknown router: {router_key}", parse_mode=None
        )
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
        await update.message.reply_text(
            "Usage: /subscribe <router> [minutes]", parse_mode=None
        )
        return

    router_key = args[0].lower()
    minutes = ctx.default_lookback
    if len(args) > 1:
        try:
            minutes = int(args[1])
        except ValueError:
            await update.message.reply_text(
                "Minutes must be a whole number.", parse_mode=None
            )
            return

    if minutes <= 0:
        await update.message.reply_text(
            "Minutes must be greater than zero.", parse_mode=None
        )
        return
    try:
        resolve_router(router_key, ctx.network, ctx.routers)
    except KeyError:
        await update.message.reply_text(
            f"Unknown router: {router_key}", parse_mode=None
        )
        return

    async with ctx.db.session() as session:
        repo = Repository(session)
        target_id = ctx.allowed_chat_id or update.effective_user.id
        user = await repo.get_or_create_user(target_id)
        await repo.add_subscription(user.id, router_key, minutes)

    await update.message.reply_text(
        f"Subscribed to {router_key} updates every {minutes} minutes.",
        parse_mode=None,
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
        await update.message.reply_text("No active subscriptions.", parse_mode=None)
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
        await update.message.reply_text("Usage: /unsubscribe <router>", parse_mode=None)
        return
    router_key = args[0].lower()
    async with ctx.db.session() as session:
        repo = Repository(session)
        target_id = ctx.allowed_chat_id or update.effective_user.id
        user = await repo.get_or_create_user(target_id)
        await repo.remove_subscription(user.id, router_key)
    await update.message.reply_text(f"Unsubscribed from {router_key}.", parse_mode=None)


async def unsubscribe_all_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    ctx = get_ctx(context)
    async with ctx.db.session() as session:
        repo = Repository(session)
        target_id = ctx.allowed_chat_id or update.effective_user.id
        user = await repo.get_or_create_user(target_id)
        await repo.remove_all_subscriptions(user.id)
    await update.message.reply_text("All subscriptions removed.", parse_mode=None)


async def watch_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    ctx = get_ctx(context)
    args = list(getattr(context, "args", []))
    if not args:
        await update.message.reply_text(
            "Usage: /watch <token_address> [symbol] [label]", parse_mode=None
        )
        return
    token_address = _normalize_token_address(args[0])
    if not token_address:
        await update.message.reply_text(
            "Provide a valid Base token address (0x-prefixed, 42 characters).",
            parse_mode=None,
        )
        return
    token_symbol = args[1] if len(args) > 1 else None
    label = " ".join(args[2:]).strip() if len(args) > 2 else None
    if label == "":
        label = None

    target_id = ctx.allowed_chat_id or update.effective_user.id
    async with ctx.db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(target_id)
        watch = await repo.add_watch_token(
            user.id,
            token_address,
            token_symbol,
            label,
        )

    descriptor = watch.token_symbol or watch.label or watch.token_address
    suffix = f" ({watch.label})" if watch.label and descriptor != watch.label else ""
    await update.message.reply_text(
        f"Watchlist updated: {descriptor}{suffix} at {token_address}.",
        parse_mode=None,
    )


async def watchlist_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    ctx = get_ctx(context)
    target_id = ctx.allowed_chat_id or update.effective_user.id

    async with ctx.db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(target_id)
        tokens = await repo.list_watch_tokens(user.id)

    if not tokens:
        await update.message.reply_text("Your watchlist is empty.", parse_mode=None)
        return

    header = escape_markdown("Your watchlist:")
    lines = []
    seen_addresses = set()
    for token in tokens:
        address = (token.token_address or "").lower()
        if address in seen_addresses:
            continue
        seen_addresses.add(address)
        display = token.token_symbol or token.label or token.token_address
        entry = (
            f"• {escape_markdown(display)} — `{escape_markdown(token.token_address)}`"
        )
        if token.label and display != token.label:
            entry += f" \\({escape_markdown(token.label)}\\)"
        lines.append(entry)
    message = "\n".join([header, *lines])
    await update.message.reply_text(message, parse_mode="MarkdownV2")


async def unwatch_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    ctx = get_ctx(context)
    args = list(getattr(context, "args", []))
    if not args:
        await update.message.reply_text(
            "Usage: /unwatch <token_address>", parse_mode=None
        )
        return
    token_address = _normalize_token_address(args[0])
    if not token_address:
        await update.message.reply_text(
            "Provide a valid Base token address (0x-prefixed, 42 characters).",
            parse_mode=None,
        )
        return

    target_id = ctx.allowed_chat_id or update.effective_user.id
    async with ctx.db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(target_id)
        await repo.remove_watch_token(user.id, token_address)

    await update.message.reply_text(
        f"Removed {token_address} from your watchlist.", parse_mode=None
    )


async def unwatch_all_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    ctx = get_ctx(context)
    target_id = ctx.allowed_chat_id or update.effective_user.id

    async with ctx.db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(target_id)
        await repo.remove_all_watch_tokens(user.id)

    await update.message.reply_text("Watchlist cleared.", parse_mode=None)


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
    await update.message.reply_text(
        f"Default lookback set to {ctx.default_lookback} minutes."
    )


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
                    await update.message.reply_text(
                        "This bot is restricted to the configured chat."
                    )
                return
        user_id = update.effective_user.id if update.effective_user else None
        if user_id not in ctx.admin_ids:
            await update.message.reply_text("Admin only.", parse_mode=None)
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
            update.message.reply_text(
                "Slow down — hit rate limit. Try again shortly.", parse_mode=None
            )
        )
    return allowed


async def history_command(update: Update, context: CallbackContext) -> None:
    """Show recent conversation history."""
    if not await ensure_user(update, context):
        return

    ctx = get_ctx(context)
    target_chat_id = ctx.allowed_chat_id or (
        update.effective_user.id if update.effective_user else None
    )

    if not target_chat_id:
        await update.message.reply_text("Unable to identify user.", parse_mode=None)
        return

    async with ctx.db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(target_chat_id)
        history = await repo.get_conversation_history(user_id=user.id, limit=10)

    if not history:
        await update.message.reply_text(
            "No conversation history found.", parse_mode=None
        )
        return

    lines = ["*Recent Conversation:*\n"]
    for msg in history:
        role_emoji = "👤" if msg.role == "user" else "🤖"
        timestamp = msg.created_at.strftime("%H:%M")
        content_preview = (
            msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        )
        content_escaped = escape_markdown(content_preview)
        lines.append(f"{role_emoji} `{timestamp}` {content_escaped}")

    response = "\n".join(lines)
    await update.message.reply_text(
        response, parse_mode="MarkdownV2", disable_web_page_preview=True
    )


async def natural_language_handler(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    if not rate_limit(update, context):
        return
    message = update.message.text
    await send_planner_response(update, context, message)


async def send_planner_response(
    update: Update, context: CallbackContext, message: str
) -> None:
    ctx = get_ctx(context)
    target_chat_id = ctx.allowed_chat_id or (
        update.effective_user.id if update.effective_user else None
    )
    user_id: int | None = None
    recent_tokens: List[Dict[str, str]] = []
    watchlist_tokens: List[Dict[str, str]] = []
    session_id: str | None = None
    conversation_history: List[Dict[str, str]] = []

    if ctx.db and target_chat_id is not None:
        async with ctx.db.session() as session:
            repo = Repository(session)
            user = await repo.get_or_create_user(target_chat_id)
            user_id = user.id

            session_id = await repo.get_or_create_session(user.id)

            await repo.save_conversation_message(
                user_id=user.id,
                role="user",
                content=message,
                session_id=session_id,
            )

            history_rows = await repo.get_conversation_history(
                user_id=user.id,
                limit=10,
                session_id=session_id,
            )
            conversation_history = [
                {"role": msg.role, "content": msg.content} for msg in history_rows
            ]

            rows = await repo.list_active_token_context(user.id)
            recent_tokens = [_serialize_token_context(row) for row in rows]
            watch_entries = await repo.list_watch_tokens(user.id)
            watchlist_tokens = [
                _serialize_watch_token(entry) for entry in watch_entries
            ]

    if watchlist_tokens:
        seen = {
            token.get("address") for token in recent_tokens if isinstance(token, dict)
        }
        for token in watchlist_tokens:
            address = token.get("address")
            if address and address in seen:
                continue
            if address:
                seen.add(address)
            recent_tokens.append(token)

    recent_router = None
    if recent_tokens:
        for token in recent_tokens:
            if token.get("source"):
                recent_router = token["source"]
                break

    payload = {
        "network": ctx.network,
        "default_lookback": ctx.default_lookback,
        "recent_tokens": recent_tokens,
        "last_router": recent_router or "",
        "watchlist_tokens": watchlist_tokens,
        "conversation_history": conversation_history,
        "user_id": user_id,
    }

    try:
        planner_result = await ctx.planner.run(message, payload)
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
    response_text = planner_result.message.strip()
    summary_tokens = planner_result.tokens

    if not update.message:
        return

    if not response_text:
        await update.message.reply_text(
            "No recent data returned for that request.",
            parse_mode=None,
            disable_web_page_preview=True,
        )
        return

    try:
        await update.message.reply_text(
            response_text,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        logger.warning("telegram_markdown_failed", error=str(exc), text=response_text)
        # Remove markdown escapes for plain text display
        plain_text = unescape_markdown(response_text)
        await update.message.reply_text(
            plain_text,
            parse_mode=None,
            disable_web_page_preview=True,
        )

    if ctx.db and user_id:
        async with ctx.db.session() as session:
            repo = Repository(session)

            if summary_tokens:
                await repo.save_token_context(user_id, summary_tokens)

            token_addresses = [
                token.get("address") for token in summary_tokens if token.get("address")
            ]

            await repo.save_conversation_message(
                user_id=user_id,
                role="assistant",
                content=response_text,
                session_id=session_id,
                tokens_mentioned=token_addresses if token_addresses else None,
            )


def _serialize_token_context(row: TokenContext) -> Dict[str, str]:
    payload = {
        "symbol": row.symbol,
        "address": row.token_address,
        "source": row.source,
    }
    if row.base_symbol:
        payload["baseSymbol"] = row.base_symbol
    if row.token_name:
        payload["name"] = row.token_name
    if row.pair_address:
        payload["pairAddress"] = row.pair_address
    if row.url:
        payload["url"] = row.url
    if row.chain_id:
        payload["chainId"] = row.chain_id
    return payload


def _serialize_watch_token(entry: TokenWatch) -> Dict[str, str]:
    symbol = entry.token_symbol or entry.label or entry.token_address
    payload = {
        "symbol": symbol,
        "address": entry.token_address,
        "source": "watchlist",
    }
    if entry.label:
        payload["label"] = entry.label
    return payload


def _normalize_token_address(value: str | None) -> str | None:
    if not value:
        return None
    address = value.strip()
    if address.startswith("0x") and len(address) == 42:
        return address.lower()
    return None


async def clear_command(update: Update, context: CallbackContext) -> None:
    """Clear all conversation history for the user."""
    if not await ensure_user(update, context):
        return

    ctx = get_ctx(context)
    target_chat_id = ctx.allowed_chat_id or (
        update.effective_user.id if update.effective_user else None
    )

    if not target_chat_id:
        await update.message.reply_text("Unable to identify user.", parse_mode=None)
        return

    async with ctx.db.session() as session:
        repo = Repository(session)
        user = await repo.get_or_create_user(target_chat_id)
        count = await repo.clear_conversation_history(user.id)

    if count > 0:
        await update.message.reply_text(
            f"✅ Conversation history cleared ({count} messages deleted). Starting fresh!",
            parse_mode=None,
        )
    else:
        await update.message.reply_text(
            "No conversation history to clear.", parse_mode=None
        )
