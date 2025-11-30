# Agentic Flow Implementation Plan

**Goal:** Transform the bot to use a full agentic flow similar to Copilot CLI, where the LLM decides which tools to call, can make multiple rounds of tool calls, and synthesizes natural responses.

**Date:** 2025-11-29

---

## Current Architecture

```
User Message
     │
     ▼
┌─────────────────┐
│ SimplePlanner   │ ◄── Pattern-based intent matching
│ (intent_matcher)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Fixed Handlers  │ ◄── _handle_trending(), _handle_pool_analytics(), etc.
│ (1-2 tool calls)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Template Format │ ◄── token_card.py formatters
└────────┬────────┘
         │
         ▼
    Response
```

**Limitations:**
1. Intent matcher decides tools, not LLM
2. Fixed tool sequences per intent
3. No multi-step reasoning
4. No parallel tool calling decided by LLM
5. Falls back to GeminiPlanner only for UNKNOWN intents

---

## Target Architecture (Copilot CLI Style)

```
User Message
     │
     ▼
┌─────────────────┐
│ AgenticPlanner  │
│ (Gemini LLM)    │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│           Think → Plan → Execute        │
│  ┌─────────────────────────────────┐    │
│  │ 1. Analyze user intent          │    │
│  │ 2. Select tools (can be many)   │    │
│  │ 3. Execute tools in parallel    │    │
│  │ 4. Analyze results              │    │
│  │ 5. Decide: more tools or done?  │    │
│  │ 6. Loop back or synthesize      │    │
│  └─────────────────────────────────┘    │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│ Natural Response│ ◄── LLM-generated synthesis
│ + Formatted Data│
└────────┬────────┘
         │
         ▼
    Response
```

---

## Implementation Phases

### Phase 1: Gemini Native Function Calling (Priority: HIGH)

**Goal:** Use Gemini's native function calling instead of JSON prompt parsing.

**Changes:**

1. **Define MCP tools as Gemini functions** (`app/tool_definitions.py`)
   ```python
   TOOL_FUNCTIONS = [
       genai.protos.FunctionDeclaration(
           name="dexpaprika_getNetworkPools",
           description="Get top liquidity pools on a network",
           parameters=genai.protos.Schema(
               type=genai.protos.Type.OBJECT,
               properties={
                   "network": genai.protos.Schema(type=genai.protos.Type.STRING),
                   "orderBy": genai.protos.Schema(type=genai.protos.Type.STRING),
                   "limit": genai.protos.Schema(type=genai.protos.Type.INTEGER),
               },
               required=["network"],
           ),
       ),
       # ... more tools
   ]
   ```

2. **Auto-generate from MCP tool schemas** (`app/mcp_client.py`)
   - MCPClient already stores `self._tools` from server initialization
   - Add method to convert MCP tool schemas to Gemini FunctionDeclarations
   ```python
   def to_gemini_functions(self) -> list[genai.protos.FunctionDeclaration]:
       """Convert MCP tools to Gemini function declarations."""
   ```

3. **Update planner to use function calling** (`app/agentic_planner.py`)
   ```python
   response = await model.generate_content(
       messages,
       tools=[genai.protos.Tool(function_declarations=functions)],
   )
   
   # Handle function calls in response
   for part in response.candidates[0].content.parts:
       if part.function_call:
           result = await execute_tool(part.function_call)
           # Feed result back to model
   ```

**Files to modify:**
- `app/mcp_client.py` - Add `to_gemini_functions()` method
- `app/agentic_planner.py` (new) - New planner using native function calling
- `app/config.py` - Add `PLANNER_MODE` setting (simple|agentic)

---

### Phase 2: Multi-Turn Tool Execution Loop (Priority: HIGH)

**Goal:** Allow LLM to make multiple rounds of tool calls until satisfied.

**Changes:**

1. **Implement ReAct-style loop**
   ```python
   async def run(self, message: str, context: dict) -> PlannerResult:
       messages = [{"role": "user", "content": message}]
       
       for iteration in range(self.max_iterations):
           response = await self._generate(messages)
           
           if response.has_function_calls():
               # Execute all function calls in parallel
               results = await self._execute_tools_parallel(response.function_calls)
               
               # Add results to conversation
               messages.append({"role": "model", "content": response})
               messages.append({"role": "function", "content": results})
           else:
               # LLM is done, has final response
               return PlannerResult(message=response.text, tokens=extracted_tokens)
       
       # Max iterations reached
       return self._synthesize_final_response(messages)
   ```

2. **Parallel tool execution**
   ```python
   async def _execute_tools_parallel(self, function_calls: list) -> list:
       tasks = [self._execute_single_tool(fc) for fc in function_calls]
       return await asyncio.gather(*tasks, return_exceptions=True)
   ```

3. **Add iteration tracking and safety limits**
   - Max 5 iterations (configurable)
   - Max 20 total tool calls per request
   - Timeout per request (60s default)

**Files to modify:**
- `app/agentic_planner.py` - Implement loop
- `app/config.py` - Add iteration/timeout settings

---

### Phase 3: Enhanced System Prompt (Priority: MEDIUM)

**Goal:** Give LLM better context about available tools and how to use them.

**Changes:**

1. **Dynamic system prompt generation**
   ```python
   def build_system_prompt(self) -> str:
       tool_docs = self._generate_tool_documentation()
       return f"""
       You are a crypto trading assistant for Base blockchain.
       
       ## Available Tools
       {tool_docs}
       
       ## Workflow
       1. Analyze the user's request
       2. Call relevant tools to gather data
       3. If more data needed, call additional tools
       4. When you have enough info, provide a helpful response
       
       ## Guidelines
       - Always check token safety with honeypot before recommending
       - Use DexPaprika for pool/liquidity data
       - Use Dexscreener for token price/trending data
       - Use Blockscout for on-chain transactions
       - Synthesize data into natural, conversational responses
       - Include relevant numbers (price, volume, liquidity)
       - Warn about risks clearly
       """
   ```

2. **Tool documentation auto-generation**
   - Parse MCP tool schemas
   - Generate human-readable descriptions
   - Include parameter examples

**Files to modify:**
- `app/agentic_planner.py` - Add prompt builder
- `prompts/agentic_system.md` (new) - Base system prompt template

---

### Phase 4: Response Synthesis (Priority: MEDIUM)

**Goal:** Generate natural, Copilot CLI-style responses.

**Changes:**

1. **Structured output for Telegram**
   - LLM generates plain text analysis
   - Post-process to add Telegram MarkdownV2 formatting
   - Include data tables where appropriate

2. **Response templates**
   ```python
   # LLM output:
   "I found 5 new pools on Base. The safest is SHARE/WETH with 0% tax..."
   
   # Post-processed:
   "*🔍 Pool Analysis*\n\n" + llm_text + "\n\n" + formatted_data_table
   ```

3. **Token extraction for context**
   - Parse tool results for token addresses
   - Store in conversation context
   - Enable "check that token" follow-ups

**Files to modify:**
- `app/agentic_planner.py` - Add synthesis methods
- `app/token_card.py` - Add `format_for_synthesis()` helpers

---

### Phase 5: Conversation Memory (Priority: LOW)

**Goal:** Maintain context across messages like Copilot CLI.

**Changes:**

1. **Conversation history in context**
   ```python
   context = {
       "conversation_history": [
           {"role": "user", "content": "check PEPE"},
           {"role": "assistant", "content": "PEPE is trading at..."},
       ],
       "recent_tokens": [
           {"symbol": "PEPE", "address": "0x...", "chain": "base"}
       ],
   }
   ```

2. **Reference resolution**
   - "that token" → last mentioned token
   - "check the second one" → parse from previous response
   - "more details" → expand on last topic

**Files to modify:**
- `app/store/` - Add conversation memory store
- `app/agentic_planner.py` - Use conversation history

---

### Phase 6: Hybrid Mode (Priority: LOW)

**Goal:** Fast path for simple queries, agentic for complex.

**Changes:**

1. **Query complexity classifier**
   ```python
   def classify_complexity(message: str) -> str:
       # Simple: single intent, direct answer
       # "price of PEPE" → SIMPLE
       
       # Complex: multi-step, analysis needed
       # "find new safe tokens on base with good liquidity" → COMPLEX
       
       if has_address(message) and is_simple_query(message):
           return "SIMPLE"
       return "COMPLEX"
   ```

2. **Route appropriately**
   ```python
   if classify_complexity(message) == "SIMPLE":
       return await simple_planner.run(message, context)
   else:
       return await agentic_planner.run(message, context)
   ```

**Files to modify:**
- `app/simple_planner.py` - Add complexity check
- `app/config.py` - Add `PLANNER_HYBRID_MODE` setting

---

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `app/agentic_planner.py` | NEW | Full agentic planner with function calling |
| `app/tool_definitions.py` | NEW | Gemini function declarations for all tools |
| `app/mcp_client.py` | MODIFY | Add `to_gemini_functions()` method |
| `app/config.py` | MODIFY | Add agentic mode settings |
| `app/simple_planner.py` | MODIFY | Add complexity routing |
| `prompts/agentic_system.md` | NEW | System prompt for agentic mode |
| `tests/test_agentic_planner.py` | NEW | Tests for agentic planner |

---

## Configuration Options

```env
# Planner mode: simple (current), agentic (new), hybrid (both)
PLANNER_MODE=agentic

# Agentic settings
AGENTIC_MAX_ITERATIONS=5
AGENTIC_MAX_TOOL_CALLS=20
AGENTIC_TIMEOUT_SECONDS=60
AGENTIC_ENABLE_PARALLEL=true

# Hybrid mode settings
HYBRID_COMPLEXITY_THRESHOLD=0.7
```

---

## Migration Path

1. **Phase 1-2:** Implement agentic planner as optional mode
2. **Phase 3-4:** Enhance prompts and synthesis
3. **Phase 5:** Add conversation memory
4. **Phase 6:** Implement hybrid routing
5. **Final:** Make agentic the default, keep simple as fallback

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Higher latency (multi-turn) | Implement parallel tool calls, caching |
| Higher API costs | Add iteration limits, cache common queries |
| LLM hallucination | Validate tool names, strict schema enforcement |
| Infinite loops | Hard iteration limit, timeout |
| Response too long for Telegram | Truncation, pagination |

---

## Success Metrics

- [ ] LLM can decide which tools to call
- [ ] Multiple tool calls per request (parallel)
- [ ] Multi-turn reasoning (think → act → observe → think)
- [ ] Natural language responses with data
- [ ] Conversation context maintained
- [ ] <3s latency for simple queries, <10s for complex

---

## Estimated Effort

| Phase | Effort | Dependencies |
|-------|--------|--------------|
| Phase 1 | 2-3 days | None |
| Phase 2 | 1-2 days | Phase 1 |
| Phase 3 | 1 day | Phase 1 |
| Phase 4 | 1 day | Phase 2 |
| Phase 5 | 2 days | Phase 4 |
| Phase 6 | 1 day | Phase 1-4 |

**Total: ~8-10 days**

---

## Next Steps

1. Approve this plan
2. Create feature branch `feature/agentic-planner`
3. Start with Phase 1: Gemini native function calling
4. Iteratively add phases, testing each

**Ready to proceed?**
