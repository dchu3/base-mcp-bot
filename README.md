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

The bot launches the Base, Dexscreener, and Honeypot MCP servers and listens for natural language requests. It uses a Gemini-powered planner to dynamically select the right tools for your questions.

**Example interactions:**
- "What's PEPE doing?"
- "Show me recent Uniswap activity"
- "Check honeypot for ZORA"
- "What are the top tokens on Base?"
- "Tell me more about that token" (uses conversation context)

### Features

- **Dynamic Tool Discovery**: The bot automatically learns available tools from connected MCP servers at startup. No manual configuration required when adding new tools.
- **Conversational Memory**: Remembers recent tokens and context, allowing for follow-up questions like "Is it safe?" or "Check the second one".
- **Intent Classification**: Distinguishes between casual chitchat and tool-based requests to save costs and reduce latency.
- **Safety Checks**: Automatically integrates Honeypot checks when analyzing tokens, with forced refinement if safety data is missing.
- **Structured Output**: Uses Gemini's native JSON mode for reliable and robust plan generation.

### Commands

- `/history` — View recent conversation history.
- `/clear` — Clear conversation history and start fresh.
- `/help` — Show available commands and capabilities.

### Prompt template

Edit `prompts/planner.md` (or point `PLANNER_PROMPT_FILE` elsewhere) to tune how the Gemini planner selects tools. The `$tool_definitions` placeholder is automatically populated with the live capabilities of your connected MCP servers.

### Tests & linting

```bash
.venv/bin/pytest
ruff check
black --check .
```
