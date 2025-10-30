# base-mcp-bot — Requirements (Python + Telegram + Gemini)

## 1) Purpose
Create a Telegram bot that uses Gemini Generative AI to interpret natural language and call two MCP servers:
- base-mcp-server (Blockscout wrapper for Base)
- dexscreener-mcp (existing MCP server)
The bot answers questions like: "Find the latest transactions on the Uniswap router and give me a Dexscreener summary of the tokens."

---

## 2) Scope
- Async Python app that connects to Telegram and an LLM (Gemini) with MCP tool calling.
- Routes requests to the right MCP tools based on user instructions.
- Ships with router address knowledge for Uniswap, Aerodrome, PancakeSwap on Base (overrideable).
- Provides subscription flows (users can follow certain routers/tokens) with SQLite persistence.
- Includes rate limiting, admin commands, and safe prompting.
- Packaged with Docker and simple deploy scripts.

Non-goals
- No private key management or on-chain writes.
- No heavy analytics DB; keep persistence to SQLite.

---

## 3) Tech Stack
- Python: 3.11+
- Telegram: python-telegram-bot (async) v21+
- Gemini: google-generativeai SDK with function calling (tools)
- MCP: client SDK (stdio sockets) or HTTP bridge if provided by servers
- DB: sqlite3 with sqlite-utils or SQLModel (simple tables)
- Config: pydantic-settings .env
- Jobs: apscheduler (for periodic scans)
- Logging: structlog

---

## 4) Configuration
Environment variables:
```
TELEGRAM_BOT_TOKEN=
GEMINI_API_KEY=

# MCP endpoints (stdio or TCP/HTTP bridge)
MCP_BASE_SERVER_CMD="npx -y base-mcp-server start"
MCP_DEXSCREENER_CMD="node /root/mcp-servers/mcp-dexscreener/index.js"

# Network selection
BASE_NETWORK=base-mainnet

# Router addresses (override defaults with JSON path if provided)
ROUTERS_JSON=./routers.base.json

# Behaviour
DEFAULT_LOOKBACK_MINUTES=30
MAX_ITEMS=20
RATE_LIMIT_PER_USER_PER_MIN=10

# Persistence
DATABASE_URL=sqlite:///./state.db
```

routers.base.json example:
```json
{
  "uniswap_v3": {
    "mainnet": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    "sepolia": "0x0000000000000000000000000000000000000000"
  },
  "aerodrome_v2": {
    "mainnet": "0xC5cf4D1A...",
    "sepolia": "0x0000000000000000000000000000000000000000"
  },
  "pancakeswap_v3": {
    "mainnet": "0x...",
    "sepolia": "0x0000000000000000000000000000000000000000"
  }
}
```

---

## 5) Core User Stories
1) Ask for latest router txns
- As a user, I can say "latest Uniswap router txns last 15 minutes" and get a concise list with token tickers and links to explorers.

2) Token summary via Dexscreener
- As a user, I can say "summarise tokens swapped on Aerodrome last 30 minutes" and get a Dexscreener snapshot (price, 24h volume/liquidity, change).

3) Subscribe/Unsubscribe
- As a user, I can "/subscribe uniswap_v3" and receive periodic updates; "/unsubscribe uniswap_v3" stops them.

4) Direct lookups
- As a user, I can ask "show me tx 0xabc..." or "ABI for 0xrouter...".

5) Admin
- As an admin, I can "/setnetwork base-sepolia", "/setlookback 45", "/setmax 50", "/routers" to print addresses.

---

## 6) Conversation & Tooling Design
- System prompt steers Gemini to prefer tool calls and concise answers.
- Tool schemas mirror MCP tools:
  - From base-mcp-server: getDexRouterActivity, getTransactionByHash, getContractABI, resolveToken.
  - From dexscreener-mcp: getTokenOverview, searchPairs, getPairByAddress.
- Planner
  - If input mentions a router name, map to address for current network.
  - First call getDexRouterActivity, gather token addresses, then call Dexscreener tool(s) for summaries.
  - Collate results into a single message (markdown) with bullet points.
- Safety
  - Refuse sensitive/illegal asks; never claim to trade or give financial advice. Include "not financial advice" footer.

---

## 7) Commands
- /start — welcome + help.
- /help — show quick examples.
- /latest <router> [minutes] — calls getDexRouterActivity.
- /summary <router> [minutes] — router txns -> token list -> Dexscreener summaries.
- /tx <hash> — transaction details.
- /abi <address> — fetch ABI.
- /subscribe <router> — add to user's watch list.
- /unsubscribe <router> — remove from watch list.
- /routers — print current router addresses for the network.
- /setnetwork <base-mainnet|base-sepolia> — admin only.

Natural-language fallback always available (Gemini planner).

---

## 8) Data Model (SQLite)
Tables (minimal):
- users(id PRIMARY KEY, chat_id UNIQUE, created_at)
- subs(user_id, router_key, lookback_minutes DEFAULT 30, PRIMARY KEY(user_id, router_key))
- settings(key PRIMARY KEY, value)
- seen_txns(tx_hash PRIMARY KEY, router_key, first_seen_at)

---

## 9) Scheduler
- Every N minutes (per subscription), call getDexRouterActivity(router, sinceMinutes).
- For unseen tx hashes, emit a summary to the subscriber.
- Backoff on errors; log and continue.

---

## 10) Output Formatting
- Telegram-friendly markdown.
- For tx lists: "• 12:04 — swapExactTokensForTokens (0.23 ETH) — 0xabc…def"
- For token summaries: "TICKER — price, 24h vol, liq, 24h %; Dexscreener link".
- Always end with "(DYOR, not financial advice)" when prices are shown.

---

## 11) Project Layout
```
/app
  main.py            # entrypoint
  config.py          # settings
  routers.py         # router mapping & helpers
  mcp_client.py      # spawn/connect to MCP servers
  planner.py         # Gemini tool-use planner
  handlers/
    commands.py      # /start /help etc.
    latest.py
    summary.py
    lookups.py
  store/
    db.py            # SQLite helpers
  utils/
    format.py
    logging.py
Dockerfile
README.md
```
---

## 12) Tests
- Unit tests for planner (routing to correct tools).
- Unit tests for formatters (repeatable snapshots).
- Integration smoke test against a running base-mcp-server (flagged).
- Mock MCP in CI with fixtures.

---

## 13) Deployment
- Docker image with a non-root user.
- Health command that sends a self-query to both MCP servers.
- Example docker-compose.yml that runs:
  - base-mcp-server
  - dexscreener-mcp
  - base-mcp-bot

---

## 14) Acceptance Criteria
- Free-text like "latest Uniswap router last 10 minutes" returns a list within 5s (warm path).
- "/summary uniswap_v3" produces Dexscreener info for tokens seen in that window.
- Subscriptions deliver periodic updates without duplicates.
- Changing network via admin command switches router map and results accordingly.

---

## 15) Nice-to-Haves
- Per-user rate limiting and quotas.
- Inline keyboard buttons to open Dexscreener / Blockscout links.
- Markdown tables for summaries when < 10 tokens.
- Simple "/export" to dump SQLite to a file for backup.
