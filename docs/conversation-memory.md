# Conversation Memory Feature

## Overview

The conversation memory feature enables multi-turn dialogues with the Base MCP bot, allowing users to ask follow-up questions that reference previous messages.

## How It Works

### Session Management
- **Session Creation**: A new session ID (UUID) is created on the first message
- **Session Reuse**: Messages within 30 minutes are grouped in the same session
- **Session Timeout**: After 30 minutes of inactivity, a new session starts

### Message Storage
- **User Messages**: Saved when received from Telegram
- **Assistant Responses**: Saved after planner execution
- **Metadata**: Includes tokens mentioned, confidence scores, session IDs
- **Retention**: Messages are kept for 24 hours, then auto-purged

### Context Injection
The planner receives the last 10 messages (5 turns) formatted as:
```
User: What's PEPE doing?
Assistant: PEPE is up 15% with $2.3M volume
User: Check honeypot for that token
```

## Usage Examples

### Follow-up Questions
```
User: What's PEPE doing?
Bot: PEPE (0xabc...) is up 15% on $2.3M volume with 234 swaps

User: Check honeypot for that token
Bot: [Resolves "that token" to 0xabc... from conversation history]
     PEPE honeypot check: SAFE_TO_TRADE
```

### Multi-Token References
```
User: Show me Uniswap activity
Bot: Top tokens: DEGEN (0xdef...), BRETT (0x789...), TOSHI (0x456...)

User: More details on the second one
Bot: [Resolves "second one" to BRETT from previous message]
     BRETT: Price $0.05, Volume $3.2M, Liquidity $890K
```

### Session Boundaries
```
User: What's happening on Base?
Bot: [Response about Base activity]

[35 minutes pass - new session created]

User: What about that token?
Bot: [No context - asks for clarification]
```

## Commands

### `/history`
View the last 10 conversation messages with timestamps:
```
Recent Conversation:

👤 18:30 What's PEPE doing?
🤖 18:30 PEPE (0xabc...) is up 15% with $2.3M volume
👤 18:31 Check honeypot for that
🤖 18:31 PEPE honeypot check: SAFE_TO_TRADE
```

## Configuration

Environment variables (optional):
```bash
CONVERSATION_RETENTION_HOURS=24          # How long to keep messages
CONVERSATION_SESSION_TIMEOUT_MINUTES=30  # Inactivity timeout
CONVERSATION_MAX_CONTEXT_MESSAGES=10     # Messages sent to Gemini
```

## Database Schema

### `conversationmessage` Table
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `user_id` | INTEGER | Foreign key to `user.id` |
| `role` | VARCHAR | "user" or "assistant" |
| `content` | TEXT | Message content |
| `created_at` | DATETIME | Timestamp (indexed) |
| `session_id` | VARCHAR | Session UUID (indexed) |
| `tool_calls` | TEXT | JSON array of MCP calls |
| `tokens_mentioned` | TEXT | JSON array of addresses |
| `confidence` | FLOAT | Planner confidence (0.0-1.0) |

### Indexes
- `user_id` - Fast user lookups
- `created_at` - Chronological queries
- `session_id` - Session filtering

## Cleanup Job

A scheduled job runs every 6 hours to purge messages older than 24 hours:
```python
scheduler.add_job(
    self._purge_old_conversations,
    "interval",
    hours=6,
    id="purge_conversations"
)
```

## Performance

- **Latency Overhead**: <50ms per request (2 DB queries)
- **Storage**: ~500 bytes per message
- **Memory**: Messages loaded on-demand, not cached
- **Scaling**: Indexed queries ensure O(log n) performance

## Prompt Template Updates

The planner prompt now includes:
```markdown
## Workflow
2. Review recent conversation history (if available): $conversation_history

## Reference Resolution
When the user references entities from previous messages:
- "that token" / "the last one" → Check conversation_history
- "more details" → Infer subject from last assistant message
- If ambiguous and confidence < 0.7, ask for clarification
```

## Testing

Run conversation memory tests:
```bash
source .venv/bin/activate
pytest tests/test_conversation_memory.py -v
```

Test coverage:
- ✅ Save and retrieve messages
- ✅ Session management
- ✅ Old message purging
- ✅ History limit enforcement

## Migration Notes

**No breaking changes** - the feature is fully backward compatible:
- Existing `TokenContext` and `TokenWatch` continue working
- Old conversations without sessions gracefully handle missing context
- Planner falls back to token context if no conversation history exists

## Troubleshooting

### "No conversation history found"
- Normal for first message or after 24 hours of inactivity
- Check `CONVERSATION_RETENTION_HOURS` setting

### Session not persisting across messages
- Verify messages are <30 minutes apart
- Check `CONVERSATION_SESSION_TIMEOUT_MINUTES`
- Review logs for `get_or_create_session` calls

### Reference resolution failing
- Ensure conversation history is being passed to planner
- Check prompt template includes `$conversation_history`
- Verify Gemini model supports context window size (10 messages ≈ 2K tokens)

## Future Enhancements

Potential improvements (not yet implemented):
- `/clear` - Manually start a new session
- Session naming ("morning-trading", "pepe-analysis")
- Export conversation history as JSON/text
- Smart summarization to compress old messages
- Cross-session memory for important tokens
