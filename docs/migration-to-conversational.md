# Migration to Conversational-Only Bot

## 🎯 Overview

This migration removes router subscriptions and watchlist features to transform the bot into a **fully conversational AI assistant**.

**Date**: 2025-11-17  
**Status**: IN PROGRESS

---

## ✅ Phase 1: Database Cleanup (COMPLETE)

### Removed Models
- ✅ `Subscription` - Router subscription tracking
- ✅ `SeenTxn` - Transaction deduplication  
- ✅ `TokenWatch` - Watchlist tokens

### Kept Models
- ✅ `User` - User accounts
- ✅ `Setting` - Configuration storage
- ✅ `TokenContext` - Conversation context (used by planner)
- ✅ `ConversationMessage` - Chat history

### Repository Methods Removed
- ✅ `list_subscriptions()`
- ✅ `all_subscriptions()`
- ✅ `add_subscription()`
- ✅ `remove_subscription()`
- ✅ `remove_all_subscriptions()`
- ✅ `mark_seen()`
- ✅ `is_seen()`
- ✅ `list_watch_tokens()`
- ✅ `add_watch_token()`
- ✅ `remove_watch_token()`
- ✅ `remove_all_watch_tokens()`
- ✅ `all_watch_tokens()`

### Repository Methods Kept
- ✅ `get_or_create_user()`
- ✅ `get_user_by_id()`
- ✅ `save_token_context()` - For planner
- ✅ `list_active_token_context()` - For planner
- ✅ `purge_expired_token_context()` - Cleanup
- ✅ Conversation memory methods (all)

---

## 🔄 Phase 2: Services (COMPLETE)

### Created
- ✅ `app/jobs/cleanup.py` - Simple cleanup service
  - Purges old conversations (every 6 hours)
  - Purges expired token context (every hour)

### To Be Removed
- ⏳ `app/jobs/subscriptions.py` - Complex subscription/watchlist service

---

## ⏳ Phase 3: Handlers (TODO)

### Commands to Remove
- [ ] `/routers` - List available routers
- [ ] `/latest <router>` - Manual router check
- [ ] `/subscribe <router>` - Start subscription
- [ ] `/subscriptions` - List active subscriptions
- [ ] `/unsubscribe <router>` - Stop subscription
- [ ] `/unsubscribe_all` - Clear all subscriptions
- [ ] `/watch <address>` - Add to watchlist
- [ ] `/watchlist` - Show watchlist
- [ ] `/unwatch <address>` - Remove from watchlist
- [ ] `/unwatch_all` - Clear watchlist

### Commands to Keep
- [ ] `/start` - Welcome message
- [ ] `/help` - Show capabilities
- [ ] `/history` - View conversation
- [ ] `/clear` - Reset conversation
- [ ] Natural language handler (main interface)

---

## ⏳ Phase 4: Command Menu (TODO)

### New Command List
```python
commands = [
    BotCommand("help", "Show what I can do"),
    BotCommand("history", "View recent conversation"),
    BotCommand("clear", "Clear conversation history"),
]
```

---

## ⏳ Phase 5: Help Text (TODO)

### New Help Message
```
I'm your Base blockchain assistant powered by AI.

💬 Just ask me questions naturally:
• "What's PEPE doing?"
• "Show me recent Uniswap activity"
• "Check honeypot for ZORA"
• "What are the top tokens on Base?"

🧠 I remember our conversation, so you can ask follow-ups like:
• "Tell me more about that token"
• "What about the second one?"

📋 Commands:
/history — view recent conversation
/clear — start fresh conversation

⚠️ All tokens can rug pull. DYOR, not financial advice.
```

---

## ⏳ Phase 6: HandlerContext (TODO)

### Simplified Context
```python
@dataclass
class HandlerContext:
    db: Database
    planner: GeminiPlanner
    rate_limiter: RateLimiter | None
    admin_ids: List[int]
    allowed_chat_id: int | None
```

**Removed**:
- `routers: Dict[str, Router]`
- `network: str`
- `default_lookback: int`
- `subscription_service: SubscriptionService | None`

---

## ⏳ Phase 7: Main App (TODO)

### Changes Needed
1. Remove `SubscriptionService` import
2. Import `CleanupService` instead
3. Remove router map loading
4. Simplify handler context creation
5. Start cleanup service instead of subscription service
6. Update command menu to 3 commands

---

## ⏳ Phase 8: Tests (TODO)

### Files to Remove
- [ ] `tests/test_subscriptions.py`

### Files to Update
- [ ] `tests/test_handlers.py` - Remove subscription/watchlist tests
- [ ] `tests/test_repository.py` - Keep only conversation/context tests

---

## ⏳ Phase 9: Documentation (TODO)

### README.md
Rewrite to emphasize conversational AI assistant.

### Remove
- Any router/watchlist documentation

### Update
- `docs/conversation-memory.md` - Already good
- Create new user guide focused on natural language

---

## 📊 Progress Summary

| Phase | Status | % Complete |
|-------|--------|-----------|
| 1. Database | ✅ Complete | 100% |
| 2. Services | ✅ Complete | 100% |
| 3. Handlers | ⏳ Pending | 0% |
| 4. Command Menu | ⏳ Pending | 0% |
| 5. Help Text | ⏳ Pending | 0% |
| 6. HandlerContext | ⏳ Pending | 0% |
| 7. Main App | ⏳ Pending | 0% |
| 8. Tests | ⏳ Pending | 0% |
| 9. Documentation | ⏳ Pending | 0% |

**Overall**: 22% Complete (2/9 phases)

---

## 🔄 Next Steps

1. **Complete handler removal** - Delete 10 command functions
2. **Update main.py** - Switch to CleanupService
3. **Update tests** - Remove obsolete tests
4. **Test manually** - Verify conversational flow works
5. **Deploy** - With user notification

---

## ⚠️ Breaking Changes

### For Users
- All `/latest`, `/subscribe`, `/watch` commands will stop working
- Existing subscriptions will be silently deleted
- Watchlists will be deleted
- **Migration Path**: Just ask questions naturally!

### Example Migration
**Old Way**:
```
/subscribe uniswap_v3 30
/watch 0xabc123 PEPE
/latest uniswap_v3 15
```

**New Way**:
```
User: What's happening on Uniswap?
User: Tell me about PEPE
User: Show me recent Uniswap swaps
```

---

## 🎉 Benefits

1. **Simpler** - 3 commands instead of 13
2. **Natural** - Just talk to it
3. **Contextual** - Remembers conversation
4. **AI-Powered** - Gemini adapts to queries
5. **Less Code** - ~1,500 lines removed
6. **Easier to Maintain** - Fewer features to debug

---

**Status**: Migration in progress. Database and services complete. Handlers and UI updates next.
