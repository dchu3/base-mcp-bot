# base-mcp-bot

Telegram bot that uses Gemini function planning to orchestrate Base and Dexscreener MCP servers.

## Getting started

```bash
./scripts/install.sh
source .venv/bin/activate
```

Populate `.env` with your Telegram bot token, Gemini API key, and MCP server commands before starting the bot.

### Run the bot

```bash
./scripts/start.sh
```

The bot launches both MCP servers, handles Telegram commands (`/latest`, `/summary`, `/subscribe`, etc.), and pushes subscription notifications on an interval. Pass additional flags (such as `--log-level`) after the script name and they will be forwarded to the Python entrypoint.

### Tests & linting

```bash
pytest
ruff check
black --check .
```
