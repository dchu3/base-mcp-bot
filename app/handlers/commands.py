"""Telegram command handlers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, List, Protocol

from telegram import Update, constants
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.planner_types import PlannerResult
from app.store.db import Database, TokenContext
from app.store.repository import Repository
from app.utils.formatting import escape_markdown, unescape_markdown
from app.utils.logging import get_logger
from app.utils.rate_limit import RateLimiter


class Planner(Protocol):
    """Protocol for planner implementations.

    Any class implementing this protocol can be used as the planner
    in HandlerContext. The run method processes user messages and
    returns structured results.
    """

    async def run(self, message: str, context: dict) -> PlannerResult:
        """Process a user message and return a response.

        Args:
            message: The user's input text.
            context: Additional context including conversation history,
                recent tokens, and network information.

        Returns:
            PlannerResult with the response message and any discovered tokens.
        """
        ...


logger = get_logger(__name__)


@dataclass
class HandlerContext:
    db: Database
    planner: Planner
    rate_limiter: RateLimiter | None
    admin_ids: List[int]
    allowed_chat_id: int | None


def setup(application: Application, handler_context: HandlerContext) -> None:
    """Register handlers on the Telegram application."""
    application.bot_data["ctx"] = handler_context

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("clear", clear_command))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, natural_language_handler)
    )


def get_ctx(context: CallbackContext) -> HandlerContext:
    return context.application.bot_data["ctx"]


async def ensure_user(update: Update, context: CallbackContext) -> bool:
    """Ensure user is allowed to use the bot."""
    ctx = get_ctx(context)
    if ctx.allowed_chat_id:
        if update.effective_chat:
            if update.effective_chat.id != ctx.allowed_chat_id:
                await update.message.reply_text(
                    "This bot is restricted to the configured chat.", parse_mode=None
                )
                return False
        else:
            await update.message.reply_text(
                "This bot is restricted to the configured chat.", parse_mode=None
            )
            return False
    return True


async def start(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    text = (
        "👋 Welcome! I'm your Base blockchain assistant.\n\n"
        "Just ask me questions naturally, like:\n"
        "• What's PEPE doing?\n"
        "• Show me recent Uniswap activity\n"
        "• Check honeypot for ZORA\n\n"
        "Type /help to learn more!"
    )
    await update.message.reply_text(text, parse_mode=None)


async def help_command(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    text = (
        "I'm your Base blockchain assistant powered by AI.\n\n"
        "💬 Just ask me questions naturally:\n"
        '• "What\'s PEPE doing?"\n'
        '• "Show me recent Uniswap activity"\n'
        '• "Check honeypot for ZORA"\n'
        '• "What are the top tokens on Base?"\n\n'
        "🧠 I remember our conversation, so you can ask follow-ups like:\n"
        '• "Tell me more about that token"\n'
        '• "What about the second one?"\n\n'
        "📋 Commands:\n"
        "/history — view recent conversation\n"
        "/clear — start fresh conversation\n\n"
        "⚠️ All tokens can rug pull. DYOR, not financial advice."
    )
    await update.message.reply_text(text, parse_mode=None)


def rate_limit(update: Update, context: CallbackContext) -> bool:
    ctx = get_ctx(context)
    user = update.effective_user
    if not user:
        return True
    if not ctx.rate_limiter:
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


async def natural_language_handler(update: Update, context: CallbackContext) -> None:
    if not await ensure_user(update, context):
        return
    if not rate_limit(update, context):
        return

    if update.effective_chat:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING
        )

    message = update.message.text
    await send_planner_response(update, context, message)


async def send_planner_response(
    update: Update, context: CallbackContext, message: str
) -> None:
    """Execute planner and send response to user."""
    ctx = get_ctx(context)
    user_id = None

    if update.effective_user:
        user_id = (
            ctx.allowed_chat_id if ctx.allowed_chat_id else update.effective_user.id
        )

    # Build payload with conversation context
    payload: Dict[str, any] = {}
    db_user_id = None
    session_id = None

    if ctx.db and user_id:
        async with ctx.db.session() as session:
            repo = Repository(session)
            user = await repo.get_or_create_user(user_id)
            db_user_id = user.id
            session_id = await repo.get_or_create_session(user.id)

            # Get token context for planner
            token_contexts = await repo.list_active_token_context(user.id)
            if token_contexts:
                payload["recent_tokens"] = [
                    _serialize_token_context(row) for row in token_contexts
                ]

            # Get conversation history for context
            history = await repo.get_conversation_history(user.id, limit=5)
            if history:
                payload["conversation_history"] = [
                    {"role": msg.role, "content": msg.content} for msg in history
                ]

    logger.info("planner_starting", message=message, user_id=user_id)

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

    # Save conversation to memory
    if ctx.db and db_user_id:
        async with ctx.db.session() as session:
            repo = Repository(session)

            # Save token context from planner
            if summary_tokens:
                await repo.save_token_context(db_user_id, summary_tokens)

            # Save user message
            await repo.save_conversation_message(
                user_id=db_user_id,
                role="user",
                content=message,
                session_id=session_id,
            )

            # Save assistant response
            await repo.save_conversation_message(
                user_id=db_user_id,
                role="assistant",
                content=response_text,
                session_id=session_id,
                tokens_mentioned=(
                    [t.get("symbol") for t in summary_tokens]
                    if summary_tokens
                    else None
                ),
            )


def _serialize_token_context(row: TokenContext) -> Dict[str, str]:
    """Convert TokenContext DB row to dict for planner payload."""
    return {
        "address": row.token_address,
        "symbol": row.symbol,
        "source": row.source or "",
        "baseSymbol": row.base_symbol or "",
        "name": row.token_name or "",
        "pairAddress": row.pair_address or "",
        "url": row.url or "",
        "chainId": row.chain_id or "",
    }
