# base-mcp-bot

Telegram bot for exploring tokens and DEX activity on Base blockchain. Powered by Gemini AI and MCP servers for Blockscout, Dexscreener, and Honeypot detection.

## Features

### 🔄 DEX Router Activity
Monitor real-time swap activity across multiple DEXs on Base:

| DEX | Versions | Aliases |
|-----|----------|---------|
| Uniswap | V2, V3, V4 | `uni`, `uniswap` |
| Aerodrome | V2 | `aero`, `aerodrome` |
| PancakeSwap | V2, V3 | `cake`, `pancake` |
| SushiSwap | V2 | `sushi`, `sushiswap` |

**Example queries:**
- "Show me recent Uniswap swaps"
- "What's happening on Aerodrome?"
- "Show me PancakeSwap V3 activity"

### 🪙 Token Cards
When viewing DEX activity, tokens are displayed in rich cards showing:
- **Price** with 24h change percentage
- **Liquidity** and **Volume** stats
- **Fully Diluted Valuation (FDV)**
- **Safety badge** from Honeypot analysis (✅ Safe / ⚠️ Caution / 🚨 Danger)
- **Tax info** (buy/sell percentages if applicable)
- **Direct link** to Dexscreener

Example card:
```
SURGE
💰 Price: $0.038980 (📉 -20.4%)
💧 Liq: $586.74K · 📊 Vol: $256.64K
📈 FDV: $38.98M
✅ Safe
📍 0xedB6...7b4D
View on Dexscreener
```

### 🛡️ Honeypot Detection
Automatic safety checks on tokens including:
- Buy/sell tax percentages
- Honeypot risk assessment
- Transfer restrictions

**Example queries:**
- "Is 0x1234... safe?"
- "Check honeypot for PEPE"

### 💬 Conversational Memory
The bot remembers context from your conversation:
- "What's PEPE doing?" → Shows token info
- "Is it safe?" → Runs honeypot check on PEPE
- "Tell me more" → Provides additional details

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show what the bot can do |
| `/routers` | View available DEX routers and aliases |
| `/history` | View recent conversation history |
| `/clear` | Clear conversation and start fresh |

## Getting Started

### Installation

```bash
./scripts/install.sh
source .venv/bin/activate
```

### Configuration

Create a `.env` file based on `.env.example`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
GEMINI_API_KEY=your_gemini_key

# MCP server commands
MCP_BASE_SERVER_CMD="node /path/to/base-mcp-server/dist/index.js start"
MCP_DEXSCREENER_CMD="node /path/to/mcp-dexscreener/index.js"
MCP_HONEYPOT_CMD="bash -lc 'cd /path/to/base-mcp-honeypot && node dist/server.js stdio'"

# Optional
GEMINI_MODEL=gemini-1.5-flash-latest
TELEGRAM_CHAT_ID=123456789  # Lock bot to single chat
PLANNER_PROMPT_FILE=./prompts/planner.md
```

### Run the Telegram Bot

```bash
./scripts/start.sh
```

The bot launches the MCP servers and listens for natural language requests.

### Run the CLI

The CLI provides the same functionality without Telegram, with **no message length restrictions**:

```bash
# Single query
python -m app.cli "show me uniswap activity"

# Interactive mode (REPL with conversation memory)
python -m app.cli --interactive

# JSON output for scripting
python -m app.cli --output json "trending tokens"

# Verbose mode for debugging
python -m app.cli --verbose "check 0x..."

# Read query from stdin
echo "show me PEPE" | python -m app.cli --stdin
```

**CLI Options:**

| Option | Description |
|--------|-------------|
| `-i, --interactive` | Start interactive REPL mode |
| `-o, --output {text,json,rich}` | Output format (default: text) |
| `-v, --verbose` | Show debug information |
| `--stdin` | Read query from stdin |
| `--no-ai` | Disable AI insights/synthesis |

**Interactive Commands:**

| Command | Description |
|---------|-------------|
| `/quit` | Exit the CLI |
| `/clear` | Clear conversation context |
| `/routers` | List available DEX routers |
| `/help` | Show available commands |

## Example Interactions

### Telegram Bot

```
You: Show me recent Uniswap V2 swaps
Bot: 🔄 Recent Uniswap V2 Swaps
     [Token cards with prices, liquidity, safety badges...]

You: What about Aerodrome?
Bot: 🔄 Recent Aerodrome V2 Swaps
     [Token cards...]

You: Is the first token safe?
Bot: ✅ Safe - No honeypot detected
     Buy Tax: 0% | Sell Tax: 0%

You: /routers
Bot: 📊 Available DEX Routers
     Uniswap: V2, V3, V4
     Aerodrome: V2
     ...
```

### CLI

```bash
$ python -m app.cli "show me uniswap activity"
⏳ Starting MCP servers...
⏳ MCP servers ready
⏳ Processing: show me uniswap activity

🔄 Recent Uniswap V2 Swaps

📊 PEPE/WETH
   Price: $0.00001234  |  24h: +15.2%
   Volume: $1.2M | Liquidity: $500K
   Safety: ✅ SAFE_TO_TRADE
   🔗 https://dexscreener.com/base/0x...

📊 WOJAK/USDC
   Price: $0.0012  |  24h: -8.3%
   ...

$ python -m app.cli --output json "trending" | jq '.tokens[0].symbol'
"PEPE"
```

## Architecture

- **SimplePlanner**: Pattern-based intent matching for common queries (router activity, token lookups)
- **Gemini AI**: Handles complex/ambiguous queries that don't match patterns
- **MCP Servers**: Blockscout (transactions), Dexscreener (token data), Honeypot (safety checks)
- **Token Cards**: Consistent formatting with automatic Dexscreener enrichment
- **Interfaces**: Telegram bot (with 4096 char limit) and CLI (unlimited output)

## Development

### Tests & Linting

```bash
pytest
ruff check
black --check .
```

### Prompt Customization

Edit `prompts/planner.md` to customize how the Gemini planner handles queries. The `$tool_definitions` placeholder is automatically populated with MCP server capabilities.
