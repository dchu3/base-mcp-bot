## Token Watchlist Feature Plan

### Goal
Allow Telegram users to maintain a personal watchlist of Base-chain tokens (symbol + contract address). The scheduler should periodically fetch on-chain transactions, Dexscreener stats, and Honeypot verdicts for each watched token and push the aggregated summary to the user, similar to existing router subscriptions.

---

### 1. Data Model & Persistence
1. Extend the SQLite schema with a `TokenWatch` table:
   - `user_id` (FK to users, part of PK)
   - `token_address` (PK; checksum-validated Base address)
   - `token_symbol` (latest known symbol, optional)
   - `label`/`notes` (user alias, optional)
   - `created_at`
2. Repository additions:
   - `add_watch_token(user_id, token_address, symbol, label=None)`
   - `remove_watch_token(user_id, token_address)`
   - `remove_all_watch_tokens(user_id)`
   - `list_watch_tokens(user_id)`
3. Migration plan:
   - Similar to `TokenContext`, auto-create table if missing.
   - Provide CLI helper or automatic DDL in `Database.init_models`.

### 2. Telegram UX & Commands
1. Commands (mirroring subscription UX):
   - `/watch <token_address> [symbol|label]` → add or update entry.
   - `/unwatch <token_address>` → remove single token.
   - `/unwatch_all` → clear watchlist.
   - `/watchlist` → list stored tokens (address + label + symbol).
2. Natural-language handler support:
   - Interpret phrases like “watch 0x… as LUNA”.
   - Allow follow-up queries (“show my watchlist”, “remove 0x123”).
3. Validation & feedback:
   - Ensure Base address format, dedupe entries.
   - Provide confirmations and error messaging (unknown token, invalid address, etc.).

### 3. Scheduler Integration
1. Extend `SubscriptionService` or create a parallel `TokenWatchService`:
   - On each cycle, fetch all watchlisted tokens (grouped per user).
   - For each token:
     - Use Base MCP to gather recent transactions (either via `resolveToken` or `getTransactions` filtered by token address).
     - Call Dexscreener (`getPairsByToken`) for price/liquidity data.
     - Call Honeypot to classify risk.
2. Aggregation logic:
   - Reuse `GeminiPlanner` helpers when possible (e.g., `_build_token_summary`).
   - Compose a message per user:
     - Section per token with on-chain activity + Dexscreener card + Honeypot verdict.
     - Append NFA footer.
3. Performance considerations:
   - Batch RPC/tool calls where possible (e.g., plan multiple Dexscreener requests).
   - Reuse caching (`TokenContext`) to avoid duplicate lookups within a cycle.

### 4. Planner & Context Updates
1. Expose watchlisted tokens to `GeminiPlanner`:
   - Include them alongside recent router tokens in the context payload so NL queries like “use Dexscreener on my watched LUNA” work naturally.
2. Add helper(s) to convert watchlist entries to planner-friendly hints (symbols, addresses, user labels).

### 5. Testing Strategy
1. Repository unit tests for CRUD operations + migrations.
2. Handler tests covering command parsing, validation, and messaging.
3. Scheduler tests mocking MCP responses to ensure summaries are sent per watched token and deduped by user.
4. Planner tests verifying watchlist context injection.
5. End-to-end scenario in integration tests (optional): add watch token → scheduler run → Telegram message content.

### 6. Rollout & Ops
1. Provide a data migration note (README/CHANGELOG) so existing deployments know a new table is introduced.
2. Consider feature flag or config toggle to enable/disable watchlist scheduler independently of router updates.
3. Update documentation:
   - `README.md` commands section.
   - `/help` command output.
4. Manual validation checklist:
   - Add/remove tokens via Telegram.
   - Observe scheduler output for watched tokens.
   - Trigger NL queries referencing watched symbols to verify planner path.

---

### Implementation Order (Suggested)
1. **DB & Repository**: define `TokenWatch` model and CRUD helpers.
2. **Handlers/Commands**: implement `/watch`, `/watchlist`, `/unwatch`, `/unwatch_all`.
3. **Scheduler**: extend background job to process watchlist tokens and send summaries.
4. **Planner Context**: feed watchlist data into Gemini payloads.
5. **Docs & Tests**: update README/help text and add full test coverage.
