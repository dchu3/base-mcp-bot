# base-mcp-bot

Telegram bot that uses Gemini function planning to orchestrate Base and Dexscreener MCP servers.

## Getting started

```bash
./scripts/install.sh
source .venv/bin/activate
```

Populate `.env` with your Telegram bot token, Gemini API key (and optional `GEMINI_MODEL` override), MCP server commands (for example `node ../base-mcp-server/dist/index.js start`, `node /path/to/mcp-dexscreener/index.js`, and `bash -lc 'cd /path/to/base-mcp-honeypot && node dist/server.js stdio'` for the honeypot server), `PLANNER_PROMPT_FILE` (defaults to `./prompts/planner.md`), and (optionally) `TELEGRAM_CHAT_ID` to lock the bot to a single chat before starting the bot.

### Run the bot

```bash
./scripts/start.sh
```

The bot launches the Base, Dexscreener, and Honeypot MCP servers, handles `/latest`, `/routers`, `/subscriptions`, `/subscribe`, `/unsubscribe`, `/unsubscribe_all`, and natural-language requests, and sends subscription updates on an interval. `/latest` automatically fetches swap activity and augments it with Dexscreener token snapshots (now annotated with Honeypot verdicts) when available. Pass additional flags (such as `--log-level`) after the script name and they will be forwarded to the Python entrypoint.

Dexscreener rows now include contextual tags based on 24h price change:

- **WATCH** — absolute move ≥5% and <15%; worth keeping an eye on.
- **ALERT** — move ≥15%; highlights strong positive momentum.
- **RISK** — move ≤−15%; flags sharp drawdowns.

Each snapshot also shows the Honeypot verdict (Safe to trade / Caution / Do not trade) with a short reason pulled from the Honeypot MCP server so Telegram alerts highlight smart-contract risk alongside price action.

The tags appear in brackets before the token pair, followed by a second line of “Signals” summarising volume, liquidity, and price move when those figures are available.

### Subscriptions

- Use `/subscribe <router> [minutes]` to store a recurring alert for the chosen router (default lookback comes from `DEFAULT_LOOKBACK_MINUTES` in `.env`).
- `/subscriptions` echoes all active alerts for the current chat, including router addresses and polling cadence.
- `/unsubscribe <router>` removes a single alert; `/unsubscribe_all` clears every stored router.
- The scheduler runs every `SCHEDULER_INTERVAL_MINUTES` (configurable in `.env`) and polls each subscription. New swaps trigger Dexscreener token snapshots (matching `/latest` formatting) so alerts stay focused on actionable liquidity and price signals.

### Prompt template

Edit `prompts/planner.md` (or point `PLANNER_PROMPT_FILE` elsewhere) to tune how the Gemini planner selects tools. Use `$message`, `$network`, `$routers`, and `$default_lookback` placeholders to inject runtime context. The prompt must still instruct Gemini to output strict JSON describing the tool calls.

### Tests & linting

```bash
pytest
ruff check
black --check .
```
