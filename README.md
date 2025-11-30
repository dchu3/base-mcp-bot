# base-mcp-bot

CLI tool for exploring tokens and DEX activity on Base blockchain. Powered by Gemini AI and MCP servers for Blockscout, Dexscreener, DexPaprika, Honeypot detection, and web search.

## Features

### 🤖 Agentic Mode (Default)
The bot now uses **full agentic mode** by default - the LLM decides which tools to call, runs them in parallel, and synthesizes natural language responses. This provides Copilot CLI-like behavior:

- **Dynamic tool selection** - LLM analyzes your query and picks the right tools
- **Parallel execution** - Multiple tool calls run simultaneously
- **Multi-turn reasoning** - Complex queries handled in multiple steps
- **Natural responses** - Results synthesized into conversational format

**Example queries:**
- "list top 10 new tokens on base with good liquidity pools"
- "check if PEPE is safe and show me its pools"
- "show me uniswap activity and find any new tokens"

### 🏊 Pool Analytics (DexPaprika)
Query liquidity pools across multiple DEXs:

- **Top pools by volume** - "show me top pools on base"
- **New pools** - "show newly created pools"
- **Token pools** - "find pools for PEPE"
- **Pool details** - OHLCV data, transactions, liquidity

**Example queries:**
- "show me newly created pools on base sorted by volume"
- "get pool details for 0x..."
- "what are the top pools on ethereum"

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

Example output:
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

### 🔍 Web Search
Search the web for token project information, news, and background:
- Project team and roadmap
- Recent news and announcements
- General crypto market trends

**Trigger keywords:**
- `search web for <query>`
- `web search <query>`
- `google <query>`
- `look up <query>`
- `find info about <query>`
- `find info on <query>`
- `find information about <query>`
- `look up <query>` (for general project, news, or market info; not for token-specific lookups)

**Example queries:**
- "search web for Bitcoin news"
- "web search DEGEN token"
- "google bitcoin"
- "web search bitcoin"
- "look up Base ecosystem"
  *(Use "look up" for general project or market info. For token details, use token-specific queries.)*

### 💬 Conversational Memory
The CLI remembers context from your conversation in interactive mode:
- "What's PEPE doing?" → Shows token info
- "Is it safe?" → Runs honeypot check on PEPE
- "Tell me more" → Provides additional details

## Getting Started

### Installation

```bash
./scripts/install.sh
source .venv/bin/activate
```

### Configuration

Create a `.env` file based on `.env.example`:

```env
GEMINI_API_KEY=your_gemini_key

# MCP server commands
MCP_BASE_SERVER_CMD="node /path/to/base-mcp-server/dist/index.js start"
MCP_DEXSCREENER_CMD="node /path/to/mcp-dexscreener/index.js"
MCP_HONEYPOT_CMD="bash -lc 'cd /path/to/base-mcp-honeypot && node dist/server.js stdio'"
MCP_WEBSEARCH_CMD="uvx duckduckgo-mcp-server"
MCP_DEXPAPRIKA_CMD="npx dexpaprika-mcp"

# Optional
GEMINI_MODEL=gemini-2.5-flash-lite

# Planner mode: "agentic" (default, LLM decides) or "simple" (pattern matching)
PLANNER_MODE=agentic

# Agentic planner settings
AGENTIC_MAX_ITERATIONS=8
AGENTIC_MAX_TOOL_CALLS=30
AGENTIC_TIMEOUT_SECONDS=90
```

### Usage

```bash
# Single query
python -m app.cli "show me uniswap activity"

# Or use the start script
./scripts/start.sh "show me uniswap activity"

# Interactive mode (REPL with conversation memory)
python -m app.cli --interactive

# JSON output for scripting
python -m app.cli --output json "trending tokens"

# Verbose mode for debugging
python -m app.cli --verbose "check 0x..."

# Read query from stdin
echo "show me PEPE" | python -m app.cli --stdin
```

### CLI Options

| Option | Description |
|--------|-------------|
| `-i, --interactive` | Start interactive REPL mode |
| `-o, --output {text,json,rich}` | Output format (default: text) |
| `-v, --verbose` | Show debug information |
| `--stdin` | Read query from stdin |
| `--no-ai` | Disable AI insights/synthesis |

### Interactive Commands

| Command | Description |
|---------|-------------|
| `/quit` | Exit the CLI |
| `/clear` | Clear conversation context |
| `/routers` | List available DEX routers |
| `/help` | Show available commands |

## Example Session

```bash
$ python -m app.cli --interactive
⏳ Starting MCP servers...
⏳ MCP servers ready
Base MCP Bot CLI - Interactive Mode
Type your queries, or use /quit to exit, /clear to reset context
--------------------------------------------------

> show me uniswap activity
⏳ Processing: show me uniswap activity

🔄 Recent Uniswap V2 Swaps

📊 PEPE/WETH
   Price: $0.00001234  |  24h: +15.2%
   Volume: $1.2M | Liquidity: $500K
   Safety: ✅ SAFE_TO_TRADE
   🔗 https://dexscreener.com/base/0x...

> is the first one safe?
⏳ Processing: is the first one safe?

✅ SAFE_TO_TRADE
Buy Tax: 0% | Sell Tax: 0%
No honeypot risks detected.

> tell me about the project
⏳ Processing: tell me about the project

PEPE is a meme token inspired by the popular Pepe the Frog meme...
[Web search results with project background]

> /quit
Goodbye!
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        User Query                           │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    AgenticPlanner                           │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Gemini 2.5 Flash Lite (Native Function Calling)    │   │
│  │  - Analyzes query                                    │   │
│  │  - Selects tools dynamically                         │   │
│  │  - Multi-turn reasoning (up to 8 iterations)         │   │
│  │  - Parallel tool execution                           │   │
│  │  - Natural language synthesis                        │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          │               │               │
          ▼               ▼               ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Blockscout │  │ Dexscreener │  │  DexPaprika │
│  (Base MCP) │  │    (MCP)    │  │    (MCP)    │
├─────────────┤  ├─────────────┤  ├─────────────┤
│ • Txns      │  │ • Search    │  │ • Pools     │
│ • Balances  │  │ • Trending  │  │ • OHLCV     │
│ • Contracts │  │ • Pairs     │  │ • Networks  │
│ • Logs      │  │ • Tokens    │  │ • DEXes     │
└─────────────┘  └─────────────┘  └─────────────┘
          │               │               │
          ▼               ▼               ▼
┌─────────────┐  ┌─────────────┐
│  Honeypot   │  │  DuckDuckGo │
│    (MCP)    │  │    (MCP)    │
├─────────────┤  ├─────────────┤
│ • Safety    │  │ • Web Search│
│ • Tax %     │  │ • News      │
│ • Risks     │  │ • Info      │
└─────────────┘  └─────────────┘
```

### Planner Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **agentic** (default) | LLM decides tools, parallel execution, multi-turn | Complex queries, exploration |
| **simple** | Pattern matching, fixed handlers | Fast, predictable responses |

### MCP Servers
- **Blockscout** - Base blockchain transactions, balances, contracts
- **Dexscreener** - Token search, trending, pairs data
- **DexPaprika** - Pool analytics, OHLCV, liquidity data
- **Honeypot** - Token safety checks, tax detection
- **DuckDuckGo** - Web search for project info

## Development

### Tests & Linting

```bash
pytest
ruff check
black --check .
```

### Prompt Customization

Edit `prompts/planner.md` to customize how the Gemini planner handles queries. The `$tool_definitions` placeholder is automatically populated with MCP server capabilities.

## License

MIT
