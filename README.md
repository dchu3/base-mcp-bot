# base-mcp-bot

Telegram bot that uses Gemini function planning to orchestrate Base and Dexscreener MCP servers.

## Getting started

```bash
./scripts/install.sh
source .venv/bin/activate
```

Populate `.env` with your Telegram bot token, Gemini API key (and optional `GEMINI_MODEL` override), MCP server commands (for example `node ../base-mcp-server/dist/index.js start` and `node /home/<user>/mcp-servers/mcp-dexscreener/index.js`), `PLANNER_PROMPT_FILE` (defaults to `./prompts/planner.md`), and (optionally) `TELEGRAM_CHAT_ID` to lock the bot to a single chat before starting the bot.

### Run the bot

```bash
./scripts/start.sh
```

The bot launches both MCP servers, handles `/latest`, `/routers`, `/subscribe`, `/unsubscribe`, and natural-language requests, and sends subscription updates on an interval. `/latest` automatically fetches swap activity and augments it with Dexscreener token snapshots when available. Pass additional flags (such as `--log-level`) after the script name and they will be forwarded to the Python entrypoint.

### Prompt template

Edit `prompts/planner.md` (or point `PLANNER_PROMPT_FILE` elsewhere) to tune how the Gemini planner selects tools. Use `$message`, `$network`, `$routers`, and `$default_lookback` placeholders to inject runtime context. The prompt must still instruct Gemini to output strict JSON describing the tool calls.

### Tests & linting

```bash
pytest
ruff check
black --check .
```
