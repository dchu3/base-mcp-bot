# Full Agentic Mode Implementation Plan

**Goal:** Make the bot fully agentic like Copilot CLI - no pattern matching shortcuts, LLM decides everything.

**Date:** 2025-11-30

---

## Problem Statement

### Current Behavior (Bot App)
```
User: "list top 10 new tokens on base with good liquidity pools"

SimplePlanner:
1. Pattern match: "pools" keyword → POOL_ANALYTICS intent
2. Call fixed handler: getNetworkPools(orderBy=volume_usd)
3. Format with template: format_pool_list()
4. Return: Generic "Top Pools on Base" with WETH/USDC

Result: Ignores "new", "top 10", no safety checks, template response
```

### Desired Behavior (Copilot CLI Style)
```
User: "list top 10 new tokens on base with good liquidity pools"

AgenticPlanner:
1. LLM analyzes: User wants NEW tokens, with LIQUIDITY, needs SAFETY check
2. LLM calls: dexpaprika_getNetworkPools(orderBy=created_at, limit=50)
3. LLM analyzes results: Filters for tokens with actual volume
4. LLM calls: honeypot_check_token() for each promising token (parallel)
5. LLM synthesizes: Natural language with table, safety verdicts, warnings
6. Return: Rich analysis with actionable insights

Result: Addresses all aspects of query, parallel tool calls, synthesized response
```

---

## Root Cause Analysis

| Component | Current | Problem |
|-----------|---------|---------|
| **Intent Detection** | Regex patterns | Loses nuance ("new", "top 10", "safe") |
| **Tool Selection** | Fixed per intent | Can't combine tools dynamically |
| **Tool Parameters** | Hardcoded defaults | Ignores user specifications |
| **Response Format** | Templates | Can't adapt to query context |
| **Multi-step Reasoning** | None | Can't refine based on results |

---

## Implementation Plan

### Phase 1: Replace SimplePlanner with AgenticPlanner (DONE ✓)

Already implemented:
- `app/agentic_planner.py` - Gemini native function calling
- `app/tool_converter.py` - MCP to Gemini function conversion
- Multi-turn loop with parallel tool execution

### Phase 2: Wire AgenticPlanner as Default

**Goal:** Make AgenticPlanner the primary planner, deprecate SimplePlanner.

**Changes:**

1. **Update main.py / cli.py to use AgenticPlanner by default**
   ```python
   if settings.planner_mode == "agentic":
       planner = AgenticPlanner(...)
   else:
       planner = SimplePlanner(...)  # Legacy fallback
   ```

2. **Remove intent matching bypass**
   - Current: `match_intent()` → fixed handler
   - New: All queries go through AgenticPlanner

3. **Update config default**
   ```env
   PLANNER_MODE=agentic  # Change default from "simple"
   ```

**Files to modify:**
- `app/cli.py` - Update planner instantiation
- `app/config.py` - Change default to "agentic"

### Phase 3: Enhance System Prompt for Better Tool Selection

**Goal:** Give LLM better guidance on when/how to use each tool.

**Changes:**

1. **Tool usage matrix in system prompt**
   ```
   ## Tool Selection Guide
   
   | User Intent | Primary Tool | Follow-up |
   |-------------|--------------|-----------|
   | "new tokens" | dexpaprika_getNetworkPools(orderBy=created_at) | honeypot_check_token |
   | "top pools" | dexpaprika_getNetworkPools(orderBy=volume_usd) | - |
   | "is X safe" | dexscreener_searchPairs → honeypot_check_token | - |
   | "trending" | dexscreener_getLatestBoostedTokens | honeypot_check_token |
   ```

2. **Parameter hints**
   ```
   ## DexPaprika Parameters
   - orderBy: "volume_usd" (default), "created_at" (for new tokens), "transactions"
   - limit: 10-50 depending on follow-up analysis needed
   - network: "base" (default), "ethereum", "solana", etc.
   ```

3. **Safety-first guideline**
   ```
   ALWAYS run honeypot_check_token before recommending any token.
   Include safety verdict prominently in response.
   ```

**Files to modify:**
- `app/agentic_planner.py` - Update AGENTIC_SYSTEM_PROMPT
- `prompts/agentic_system.md` (new) - Externalize prompt for easy tuning

### Phase 4: Improve Response Synthesis

**Goal:** Generate Copilot CLI-style responses with tables and structure.

**Changes:**

1. **Add synthesis guidelines to system prompt**
   ```
   ## Response Format
   
   For token lists, use this structure:
   - Summary line (e.g., "Found 4 safe new tokens out of 10 checked")
   - Table with key metrics
   - Safety verdicts with emoji (✅ Safe, ⚠️ Caution, 🚨 Avoid)
   - Warnings section if any risks
   - Links to Dexscreener
   
   Be conversational but data-rich. Include numbers.
   ```

2. **Post-processing for Telegram**
   - Convert markdown tables to Telegram-compatible format
   - Escape special characters
   - Truncate if too long

**Files to modify:**
- `app/agentic_planner.py` - Add synthesis post-processing
- `app/utils/formatting.py` - Add table formatting helpers

### Phase 5: Increase Iteration and Tool Limits

**Goal:** Allow more complex multi-step reasoning.

**Changes:**

1. **Increase defaults**
   ```python
   DEFAULT_MAX_ITERATIONS = 8  # Was 5
   DEFAULT_MAX_TOOL_CALLS = 30  # Was 20
   DEFAULT_TIMEOUT_SECONDS = 90  # Was 60
   ```

2. **Smart truncation of results**
   - Keep full data for tokens being analyzed
   - Summarize/truncate for context-only data

**Files to modify:**
- `app/agentic_planner.py` - Update defaults
- `app/config.py` - Update validation ranges

### Phase 6: Remove Legacy SimplePlanner (Optional)

**Goal:** Clean up codebase once agentic mode is proven.

**Changes:**
- Remove `app/simple_planner.py`
- Remove `app/intent_matcher.py`
- Update imports and tests

---

## File Changes Summary

| File | Change | Priority |
|------|--------|----------|
| `app/cli.py` | Use AgenticPlanner by default | HIGH |
| `app/config.py` | Change PLANNER_MODE default to "agentic" | HIGH |
| `app/agentic_planner.py` | Enhance system prompt, increase limits | HIGH |
| `prompts/agentic_system.md` | New external prompt file | MEDIUM |
| `app/utils/formatting.py` | Add table formatting | MEDIUM |
| `.env.example` | Update documentation | LOW |

---

## Configuration Changes

```env
# Change default
PLANNER_MODE=agentic

# Increase limits for complex queries
AGENTIC_MAX_ITERATIONS=8
AGENTIC_MAX_TOOL_CALLS=30
AGENTIC_TIMEOUT_SECONDS=90
```

---

## Expected Behavior After Implementation

### Query: "list top 10 new tokens on base with good liquidity pools"

```
🆕 New Tokens on Base with Liquidity

Found 4 safe tokens out of 10 newest pools checked.

## ✅ Safe Tokens

| Token | Pool | 24h Vol | Liquidity | Safety |
|-------|------|---------|-----------|--------|
| SHARE | SHARE/WETH | $8.5M | $125K | ✅ 0% tax |
| Talentir | Talentir/WETH | $4.8M | $182K | ✅ 0% tax |
| SURGE | SURGE/WETH | $1.6M | $33K | ✅ 0% tax |
| SXAI | SXAI/WETH | $1.3M | $60K | ✅ 0% tax |

## 🚨 Avoid

| Token | Issue |
|-------|-------|
| TYSM | Honeypot - 100% sell tax |
| SEXCOIN | High fail rate (22% sells fail) |
| Zoomer | High fail rate (32% sells fail) |

⚠️ These are <48 hour old tokens. High risk even if "safe".
Only invest what you can afford to lose. DYOR!

🔗 [View on Dexscreener](https://dexscreener.com/base)
```

---

## Success Metrics

- [ ] LLM correctly interprets "new" → orderBy=created_at
- [ ] LLM calls honeypot for each discovered token
- [ ] Response includes safety verdicts
- [ ] Tables render correctly in Telegram
- [ ] Response time <15s for complex queries
- [ ] No regressions on simple queries

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Higher latency | Acceptable for personal use; parallel calls help |
| Higher API cost | Acceptable for personal use; can add caching later |
| LLM hallucination | Strict function calling; validate tool names |
| Response too long | Add truncation with "...see more" |

---

## Implementation Order

1. **Phase 2**: Wire AgenticPlanner as default (1-2 hours)
2. **Phase 3**: Enhance system prompt (1 hour)
3. **Phase 4**: Improve response synthesis (2 hours)
4. **Phase 5**: Increase limits (30 mins)
5. **Testing**: End-to-end validation (1 hour)

**Total: ~6-7 hours**

---

## Next Steps

1. Approve this plan
2. Implement Phase 2-5 on `feature/agentic-planner` branch
3. Test with various queries
4. Merge to main when stable

**Ready to proceed?**
