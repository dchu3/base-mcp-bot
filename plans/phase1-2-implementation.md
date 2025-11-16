# Phase 1-2 Implementation Plan: Enhanced Prompt Engineering & Multi-Stage Planning

**Status**: Awaiting approval  
**Created**: 2025-11-16  
**Target completion**: 2-3 weeks  
**Priority**: High impact, foundational improvements

---

## Executive Summary

This plan implements **Phase 1** (Enhanced Prompt Engineering) and **Phase 2** (Multi-Stage Planning) from the Gemini planner enhancement roadmap. These changes will improve planning accuracy, reduce JSON parse failures, and enable self-correction without requiring migration to new Gemini APIs.

**Key Benefits:**
- 30-50% reduction in planning failures via few-shot examples
- Iterative refinement catches incomplete initial plans
- Chain-of-thought reasoning improves debugging & observability
- Foundation for Phase 3 (Function Calling API migration)

**Risk Level**: Low — changes are additive, backward-compatible, and feature-flagged

---

## Phase 1: Enhanced Prompt Engineering

### 1.1 Dynamic Tool Schema Generation

**Goal**: Auto-generate tool documentation from MCP introspection instead of hardcoding schemas in prompt templates.

#### Changes Required

**File**: `app/mcp_client.py`
- Add method `async def list_tools(self) -> List[ToolSchema]` to `MCPClient` class
- Call MCP's `tools/list` method during initialization
- Cache results for prompt building

**File**: `app/planner.py`
- Add `async def _build_dynamic_tool_schema(self) -> str` method
- Call during `__init__` to populate `self._tool_schemas_json`
- Inject into prompt template via `$available_tools` placeholder

#### Implementation Steps

```python
# app/mcp_client.py additions

@dataclass
class ToolSchema:
    """MCP tool metadata for prompt construction."""
    name: str
    description: str
    input_schema: Dict[str, Any]

class MCPClient:
    async def list_tools(self) -> List[ToolSchema]:
        """Fetch available tools from MCP server."""
        await self.start()
        result = await self._send_request("tools/list", {})
        
        tools = result.get("tools", [])
        schemas = []
        for tool in tools:
            schemas.append(ToolSchema(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {})
            ))
        return schemas
```

```python
# app/planner.py additions

class GeminiPlanner:
    async def _build_dynamic_tool_schema(self) -> str:
        """Generate tool documentation from MCP introspection."""
        all_schemas = []
        
        for client_name in ["base", "dexscreener", "honeypot"]:
            client = getattr(self.mcp_manager, client_name, None)
            if not client:
                continue
            
            tools = await client.list_tools()
            for tool in tools:
                all_schemas.append({
                    "client": client_name,
                    "method": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema.get("properties", {}),
                    "required": tool.input_schema.get("required", [])
                })
        
        return json.dumps(all_schemas, indent=2)
```

**Testing**:
- Unit test: Mock MCP `tools/list` response, verify schema formatting
- Integration test: Start real MCP servers, confirm schemas extracted
- Add to `tests/test_planner.py`: `test_build_dynamic_tool_schema()`

#### Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| MCP server doesn't support `tools/list` | High | Fallback to hardcoded schemas with warning log |
| Large schema exceeds prompt limits | Medium | Truncate descriptions to 100 chars, summarize params |
| Schema introspection adds startup latency | Low | Cache for 1 hour, refresh async |

---

### 1.2 Few-Shot Examples in Prompt

**Goal**: Reduce ambiguity in Gemini's tool selection by providing 5-7 concrete examples.

#### Changes Required

**File**: `prompts/planner.md`
- Add new section after workflow: `## Examples`
- Include 7 diverse user intents covering:
  1. Router activity lookup
  2. Token symbol search
  3. Cached token context usage
  4. Multi-tool chaining (router → Dexscreener → honeypot)
  5. Ambiguous request requiring clarification
  6. Transaction hash lookup
  7. Contract ABI inspection

#### Implementation

**prompts/planner.md additions**:
```markdown
## Examples

### Example 1: Router Activity
User: "Show me Uniswap V3 activity last hour"
→ {"tools": [{"client": "base", "method": "getDexRouterActivity", "params": {"router": "uniswap_v3", "sinceMinutes": 60}}]}

### Example 2: Token Search
User: "Check PEPE on Dexscreener"
→ {"tools": [{"client": "dexscreener", "method": "searchPairs", "params": {"query": "PEPE"}}]}

### Example 3: Cached Token Context
User: "Give me an update on that LUNA token from earlier"
Context: recent_tokens = [{"symbol": "LUNA", "address": "0xabc...", "chainId": "base"}]
→ {"tools": [{"client": "dexscreener", "method": "getPairsByToken", "params": {"chainId": "base", "tokenAddress": "0xabc..."}}, {"client": "honeypot", "method": "check_token", "params": {"address": "0xabc...", "chainId": 8453}}]}

### Example 4: Multi-Tool Discovery
User: "What's moving on Base?"
→ {"tools": [{"client": "base", "method": "getDexRouterActivity", "params": {"router": "uniswap_v3", "sinceMinutes": 30}}]}
Note: Planner will auto-discover tokens from results and call Dexscreener + honeypot in execute phase.

### Example 5: Transaction Lookup
User: "Analyze tx 0xabc123..."
→ {"tools": [{"client": "base", "method": "getTransactionByHash", "params": {"hash": "0xabc123..."}}]}

### Example 6: Ambiguous Request (Return Empty Tools)
User: "Tell me something interesting"
→ {"tools": []}
Response: "Please specify a router, token symbol, or transaction hash."

### Example 7: Contract Inspection
User: "Show me the ABI for 0xdef456"
→ {"tools": [{"client": "base", "method": "getContractABI", "params": {"address": "0xdef456"}}]}
```

**File**: `app/planner.py`
- No code changes needed — prompt template already loads from `prompts/planner.md`
- Verify `_build_prompt()` correctly substitutes placeholders

**Testing**:
- Manual test each example against live Gemini API
- Measure JSON parse success rate before/after (expect 15-20% improvement)
- Add to CI: Compare example outputs against expected tool selections

---

### 1.3 Chain-of-Thought Reasoning

**Goal**: Have Gemini explain its reasoning before outputting JSON plan, improving debuggability.

#### Changes Required

**File**: `prompts/planner.md`
- Add reasoning instructions before JSON schema requirement:
```markdown
Before generating your JSON plan, first think through:
1. What is the user asking for? (Restate in one sentence)
2. What information do I need to answer this? (List gaps)
3. Which tools address each gap? (Map tools to gaps)

Then output your reasoning in a <reasoning> XML block, followed by the JSON plan.
```

**File**: `app/planner.py`
- Modify `_extract_response_text()` to handle XML reasoning blocks
- Log reasoning separately for observability
- Extract JSON from remainder

#### Implementation

```python
# app/planner.py modifications

class GeminiPlanner:
    @staticmethod
    def _extract_reasoning_and_json(text: str) -> Tuple[str, str]:
        """Separate chain-of-thought reasoning from JSON payload."""
        reasoning_match = re.search(
            r"<reasoning>(.*?)</reasoning>",
            text,
            re.DOTALL | re.IGNORECASE
        )
        
        reasoning = ""
        if reasoning_match:
            reasoning = reasoning_match.group(1).strip()
            # Remove reasoning block from text
            text = text[:reasoning_match.start()] + text[reasoning_match.end():]
        
        return reasoning, text.strip()
    
    async def _plan(self, message: str, context: Dict[str, Any]) -> List[ToolInvocation]:
        # ... existing prompt building ...
        
        response = await asyncio.to_thread(...)
        raw_text = self._extract_response_text(response)
        
        reasoning, json_text = self._extract_reasoning_and_json(raw_text)
        
        if reasoning:
            logger.info("planner_reasoning", reasoning=reasoning, message=message)
        
        # Continue with existing JSON parsing logic
        try:
            payload = json.loads(self._strip_code_fence(json_text))
        except json.JSONDecodeError:
            logger.error("planner_invalid_json", output=json_text, reasoning=reasoning)
            return []
        
        # ... rest of existing logic ...
```

**Testing**:
- Unit test: `test_extract_reasoning_and_json()` with various XML formats
- Integration test: Verify reasoning is logged but doesn't break JSON parsing
- Manually inspect logs to confirm reasoning quality

#### Success Metrics

- 100% of planner calls log reasoning (when present)
- JSON parse failures include reasoning context in error logs
- Reasoning length averages 50-150 words

---

### 1.4 Confidence Scoring

**Goal**: Detect when Gemini is uncertain and should ask clarifying questions.

#### Changes Required

**File**: `prompts/planner.md`
- Add to JSON schema:
```markdown
Response schema:
{
  "confidence": <float 0.0-1.0>,
  "clarification": "<optional question if confidence < 0.7>",
  "tools": [...]
}
```

**File**: `app/planner.py`
- Parse `confidence` and `clarification` fields
- If confidence < 0.7, return `PlannerResult` with clarification message
- Log confidence distribution for tuning threshold

#### Implementation

```python
# app/planner.py modifications

@dataclass
class PlanPayload:
    """Parsed planner response from Gemini."""
    confidence: float
    clarification: str | None
    tools: List[ToolInvocation]

class GeminiPlanner:
    CONFIDENCE_THRESHOLD = 0.7
    
    async def _plan(self, message: str, context: Dict[str, Any]) -> PlanPayload:
        # ... existing logic ...
        
        payload = json.loads(self._strip_code_fence(json_text))
        
        confidence = float(payload.get("confidence", 1.0))
        clarification = payload.get("clarification")
        
        logger.info("planner_confidence", confidence=confidence, message=message)
        
        invocations = []
        for entry in payload.get("tools", []):
            # ... existing tool parsing ...
        
        return PlanPayload(
            confidence=confidence,
            clarification=clarification,
            tools=invocations
        )
    
    async def run(self, message: str, context: Dict[str, Any]) -> PlannerResult:
        plan_payload = await self._plan(message, context)
        
        if plan_payload.confidence < self.CONFIDENCE_THRESHOLD:
            clarification_msg = (
                plan_payload.clarification or 
                "I'm not sure I understood that. Could you rephrase?"
            )
            logger.info(
                "planner_requesting_clarification",
                confidence=plan_payload.confidence,
                question=clarification_msg
            )
            return PlannerResult(message=clarification_msg, tokens=[])
        
        if not plan_payload.tools:
            # ... existing "no plan" logic ...
        
        results = await self._execute_plan(plan_payload.tools, context)
        return self._render_response(message, context, results)
```

**Configuration**:
- Add to `app/config.py`:
```python
planner_confidence_threshold: float = Field(
    default=0.7,
    alias="PLANNER_CONFIDENCE_THRESHOLD",
    ge=0.0,
    le=1.0
)
```

**Testing**:
- Mock test: Inject confidence values [0.3, 0.6, 0.8, 1.0], verify behavior
- Integration test: Provide ambiguous prompts ("do something"), expect clarification
- Metrics dashboard: Track confidence distribution over 1 week

#### Success Metrics

- < 5% of requests trigger clarification (avoid over-caution)
- 0% of clarifications go unanswered (indicates broken UX)
- Average confidence for successful plans: > 0.85

---

## Phase 2: Multi-Stage Planning

### 2.1 Iterative Planner with Reflection

**Goal**: Allow Gemini to refine its plan based on initial results, catching incomplete tool selections.

#### Changes Required

**File**: `app/planner.py`
- Rename `run()` → `run_single_stage()`
- Add new `run()` that orchestrates 1-2 planning iterations
- Add `_is_plan_complete()` heuristic to decide if refinement is needed
- Add `_refine_plan()` to generate follow-up tool calls

#### Implementation

```python
# app/planner.py additions

class GeminiPlanner:
    MAX_PLANNING_ITERATIONS = 2
    
    async def run(
        self,
        message: str,
        context: Dict[str, Any],
        enable_reflection: bool = True
    ) -> PlannerResult:
        """Execute plan with optional iterative refinement."""
        iteration = 1
        all_results = []
        
        # Initial planning pass
        plan_payload = await self._plan(message, context)
        
        if plan_payload.confidence < self.CONFIDENCE_THRESHOLD:
            return self._build_clarification_result(plan_payload)
        
        if not plan_payload.tools:
            return self._build_empty_plan_result()
        
        results = await self._execute_plan(plan_payload.tools, context)
        all_results.extend(results)
        
        # Check if refinement is needed and enabled
        if not enable_reflection or iteration >= self.MAX_PLANNING_ITERATIONS:
            return self._render_response(message, context, all_results)
        
        if self._is_plan_complete(results, message, plan_payload.tools):
            logger.info("planner_complete_first_pass", message=message)
            return self._render_response(message, context, all_results)
        
        # Refinement pass
        logger.info("planner_attempting_refinement", iteration=iteration+1)
        refined_plan = await self._refine_plan(message, context, all_results)
        
        if refined_plan.tools:
            refined_results = await self._execute_plan(refined_plan.tools, context)
            all_results.extend(refined_results)
        
        return self._render_response(message, context, all_results)
    
    def _is_plan_complete(
        self,
        results: List[Dict[str, Any]],
        message: str,
        tools_called: List[ToolInvocation]
    ) -> bool:
        """Heuristic to determine if initial plan was sufficient."""
        # Check for errors in critical calls
        has_errors = any("error" in r for r in results)
        if has_errors:
            return False
        
        # Check if user asked about tokens but no Dexscreener calls were made
        token_intent_keywords = ["token", "price", "dex", "pair", "liquidity"]
        user_wants_tokens = any(kw in message.lower() for kw in token_intent_keywords)
        
        dex_calls = [t for t in tools_called if t.client == "dexscreener"]
        if user_wants_tokens and not dex_calls:
            return False
        
        # Check if router activity was fetched but no token analysis followed
        router_calls = [t for t in tools_called if t.method == "getDexRouterActivity"]
        if router_calls and not dex_calls:
            # Should have discovered tokens and called Dexscreener
            discovered_tokens = any(
                self._extract_token_entries(r.get("result", {}))
                for r in results
            )
            if discovered_tokens:
                return False
        
        # Default: plan is complete
        return True
    
    async def _refine_plan(
        self,
        message: str,
        context: Dict[str, Any],
        prior_results: List[Dict[str, Any]]
    ) -> PlanPayload:
        """Generate follow-up tool calls based on initial results."""
        results_summary = self._summarize_results_for_refinement(prior_results)
        
        refinement_prompt = self._build_refinement_prompt(
            message,
            context,
            results_summary
        )
        
        logger.info("planner_refinement_prompt", prompt=refinement_prompt)
        
        response = await asyncio.to_thread(
            self.model.generate_content,
            [{"role": "user", "parts": [{"text": refinement_prompt}]}]
        )
        
        text = self._extract_response_text(response)
        reasoning, json_text = self._extract_reasoning_and_json(text)
        
        if reasoning:
            logger.info("planner_refinement_reasoning", reasoning=reasoning)
        
        try:
            payload = json.loads(self._strip_code_fence(json_text))
        except json.JSONDecodeError:
            logger.error("planner_refinement_invalid_json", output=json_text)
            return PlanPayload(confidence=0.0, clarification=None, tools=[])
        
        # Parse tools
        invocations = []
        for entry in payload.get("tools", []):
            client = entry.get("client")
            method = entry.get("method")
            params = self._normalize_params(
                client,
                method,
                entry.get("params", {}),
                context.get("network")
            )
            if client not in {"base", "dexscreener", "honeypot"} or not method:
                continue
            invocations.append(
                ToolInvocation(client=client, method=method, params=params)
            )
        
        return PlanPayload(
            confidence=1.0,  # Refinement doesn't use confidence
            clarification=None,
            tools=invocations
        )
    
    def _summarize_results_for_refinement(
        self,
        results: List[Dict[str, Any]]
    ) -> str:
        """Create concise summary of tool call results for refinement prompt."""
        summary_lines = []
        
        for entry in results:
            call = entry["call"]
            status = "ERROR" if "error" in entry else "SUCCESS"
            
            summary = f"{call.client}.{call.method}: {status}"
            
            if status == "SUCCESS":
                result = entry.get("result", {})
                if isinstance(result, dict):
                    # Extract key metrics
                    if "items" in result:
                        summary += f" ({len(result['items'])} items)"
                    if call.client == "dexscreener":
                        tokens = entry.get("tokens", [])
                        if tokens:
                            symbols = [t.get("symbol", "?") for t in tokens[:3]]
                            summary += f" (tokens: {', '.join(symbols)})"
                elif isinstance(result, list):
                    summary += f" ({len(result)} items)"
            else:
                summary += f" ({entry['error'][:50]})"
            
            summary_lines.append(summary)
        
        return "\n".join(summary_lines)
    
    def _build_refinement_prompt(
        self,
        message: str,
        context: Dict[str, Any],
        results_summary: str
    ) -> str:
        """Construct prompt asking Gemini if additional tools are needed."""
        return textwrap.dedent(f"""
            Original user request: "{message}"
            
            I already executed these tools:
            {results_summary}
            
            Based on the results above, should I call additional tools to fully answer the user's request?
            
            If YES: Output a JSON plan with new tool calls (don't repeat calls already made).
            If NO: Output {{"tools": []}}
            
            Available tools: base.getDexRouterActivity, base.getTransactionByHash, base.getContractABI, base.resolveToken, dexscreener.getTokenOverview, dexscreener.searchPairs, dexscreener.getPairByAddress, honeypot.check_token
            
            Respond with JSON only.
        """).strip()
```

**Configuration**:
- Add feature flag to `app/config.py`:
```python
planner_enable_reflection: bool = Field(
    default=True,
    alias="PLANNER_ENABLE_REFLECTION"
)
```

- Add to `.env.example`:
```
PLANNER_ENABLE_REFLECTION=true
```

**Testing**:
- Unit test: `test_is_plan_complete()` with various result scenarios
- Mock test: Verify refinement prompt construction
- Integration test: Provide incomplete plan scenario (router activity without Dexscreener), verify refinement triggers
- Regression test: Ensure complete plans don't trigger unnecessary refinement

#### Success Metrics

- < 20% of requests trigger refinement (indicates good first-pass planning)
- 90% of refined plans are "complete" (no third iteration needed)
- Average tool calls per request increases from 2.5 → 3.2 (healthy discovery)

---

### 2.2 Result-Aware Context Injection

**Goal**: Pass summaries of prior tool results back into planning context to inform subsequent calls.

#### Changes Required

**File**: `app/planner.py`
- Modify `_build_prompt()` to accept `prior_results` parameter
- Add `$prior_results` placeholder to template
- Format prior results as concise bullet points

#### Implementation

```python
# app/planner.py modifications

class GeminiPlanner:
    def _build_prompt(
        self,
        message: str,
        context: Dict[str, Any],
        prior_results: List[Dict[str, Any]] | None = None
    ) -> str:
        """Build planner prompt with optional prior results context."""
        routers = ", ".join(self.router_keys) or "none"
        token_hint = self._format_recent_tokens(context.get("recent_tokens") or [])
        last_router = context.get("last_router") or "unknown"
        
        context_map = {
            "message": message,
            "network": context.get("network", "base"),
            "routers": routers,
            "default_lookback": context.get("default_lookback", 30),
            "recent_tokens": token_hint,
            "recent_router": last_router,
            "prior_results": self._format_prior_results(prior_results) if prior_results else "none"
        }
        
        prompt = self._prompt_template.safe_substitute(context_map)
        
        if "$" in prompt:
            logger.warning("prompt_unresolved_placeholders", prompt=prompt)
        
        return prompt
    
    def _format_prior_results(self, results: List[Dict[str, Any]]) -> str:
        """Format prior tool call results for injection into prompt."""
        if not results:
            return "none"
        
        lines = []
        for entry in results:
            call = entry["call"]
            if "error" in entry:
                lines.append(f"- {call.client}.{call.method}: FAILED")
            else:
                result = entry.get("result", {})
                if isinstance(result, dict) and "items" in result:
                    count = len(result["items"])
                    lines.append(f"- {call.client}.{call.method}: {count} transactions")
                elif call.client == "dexscreener":
                    tokens = entry.get("tokens", [])
                    if tokens:
                        symbols = [t.get("symbol", "?") for t in tokens[:2]]
                        lines.append(f"- {call.client}.{call.method}: {', '.join(symbols)}")
                    else:
                        lines.append(f"- {call.client}.{call.method}: SUCCESS")
                else:
                    lines.append(f"- {call.client}.{call.method}: SUCCESS")
        
        return "\n".join(lines)
```

**File**: `prompts/planner.md`
- Add to workflow section:
```markdown
5. If prior results are available: $prior_results
   Consider what information has already been fetched to avoid redundant calls.
```

**Testing**:
- Unit test: `test_format_prior_results()` with various result shapes
- Integration test: Execute two-stage plan, verify second prompt includes prior results summary
- Regression test: Single-stage plans don't break with `prior_results=None`

---

## Configuration Changes

### New Environment Variables

Add to `.env.example`:
```bash
# Planner enhancements (Phase 1-2)
PLANNER_CONFIDENCE_THRESHOLD=0.7
PLANNER_ENABLE_REFLECTION=true
PLANNER_MAX_ITERATIONS=2
```

### Config Schema Updates

**File**: `app/config.py`
```python
class Settings(BaseSettings):
    # ... existing fields ...
    
    planner_confidence_threshold: float = Field(
        default=0.7,
        alias="PLANNER_CONFIDENCE_THRESHOLD",
        ge=0.0,
        le=1.0,
    )
    planner_enable_reflection: bool = Field(
        default=True,
        alias="PLANNER_ENABLE_REFLECTION"
    )
    planner_max_iterations: int = Field(
        default=2,
        alias="PLANNER_MAX_ITERATIONS",
        ge=1,
        le=5
    )
```

---

## Testing Strategy

### Unit Tests

**File**: `tests/test_planner.py`

New test cases:
1. `test_extract_reasoning_and_json()` — XML parsing edge cases
2. `test_parse_confidence_field()` — Confidence extraction & defaults
3. `test_is_plan_complete_heuristics()` — All completion scenarios
4. `test_summarize_results_for_refinement()` — Result formatting
5. `test_format_prior_results()` — Context injection formatting
6. `test_build_dynamic_tool_schema()` — MCP introspection mocking

Estimated: **~200 lines of new test code**

### Integration Tests

**File**: `tests/test_planner_integration.py` (new file)

Scenarios:
1. **Multi-stage discovery**: User asks "What's moving on Base?" → Should trigger router activity → Dexscreener → honeypot in 1-2 iterations
2. **Ambiguous input**: User says "Help me trade" → Should return clarification request
3. **Schema introspection**: Start MCP servers, verify tool schemas are correctly extracted
4. **Reasoning logging**: Verify `<reasoning>` blocks are parsed and logged

Requires: Docker compose with test MCP servers

### Manual Validation

**Checklist before merge**:
- [ ] Run 10 diverse user prompts, verify no regressions
- [ ] Check confidence scores are reasonable (avg > 0.8)
- [ ] Confirm refinement triggers < 20% of the time
- [ ] Inspect reasoning quality in logs for 5 requests
- [ ] Test with `PLANNER_ENABLE_REFLECTION=false` to ensure backward compatibility

---

## Rollout Plan

### Week 1: Phase 1.1-1.2
- Implement dynamic tool schema generation
- Add few-shot examples to `prompts/planner.md`
- Unit + integration tests
- Deploy to dev environment
- Gather 100 sample requests for baseline

### Week 2: Phase 1.3-1.4
- Implement chain-of-thought reasoning
- Add confidence scoring
- Test ambiguous inputs
- Deploy to staging
- A/B test: 50% with new features, 50% baseline

### Week 3: Phase 2.1-2.2
- Implement iterative planning
- Add result-aware context injection
- Full integration testing
- Deploy to production with feature flag `PLANNER_ENABLE_REFLECTION=false`
- Monitor for 3 days, then enable for 10% of users

### Week 4: Monitoring & Tuning
- Analyze confidence distribution → Tune threshold
- Review refinement trigger rate → Adjust `_is_plan_complete()` heuristics
- Collect failure cases → Refine prompt examples
- Full rollout to 100% of users

---

## Observability & Metrics

### New Log Events

```python
# app/utils/logging.py additions

logger.info("planner_reasoning", reasoning=str, message=str)
logger.info("planner_confidence", confidence=float, message=str)
logger.info("planner_requesting_clarification", confidence=float, question=str)
logger.info("planner_complete_first_pass", message=str)
logger.info("planner_attempting_refinement", iteration=int)
logger.info("planner_refinement_reasoning", reasoning=str)
logger.error("planner_refinement_invalid_json", output=str)
```

### Dashboards

Track in monitoring system (e.g., Grafana):
1. **JSON parse success rate** (before/after deployment)
2. **Average confidence score** (target: > 0.85)
3. **Clarification request rate** (target: < 5%)
4. **Refinement trigger rate** (target: < 20%)
5. **Average tool calls per request** (expect slight increase)
6. **Planning latency** (p50, p95, p99)

### Success Criteria

After 1 week in production:
- [ ] JSON parse failures reduced by ≥ 20%
- [ ] User satisfaction (measured by follow-up "thank you" messages) increases
- [ ] No increase in average latency > 200ms
- [ ] Refinement logic triggers on appropriate requests (manual review of 20 samples)

---

## Risks & Rollback Plan

### Identified Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Gemini ignores reasoning instructions | Medium | Low | Log warnings, doesn't break functionality |
| Confidence scores are miscalibrated | High | Medium | Start with threshold=0.5, tune based on data |
| Refinement causes latency spikes | Low | High | Feature flag allows instant disable |
| Prompt length exceeds Gemini limits | Low | Medium | Truncate tool schemas, limit prior results to 5 items |

### Rollback Procedure

1. Set `PLANNER_ENABLE_REFLECTION=false` via config (no redeployment)
2. If JSON parsing breaks, revert `prompts/planner.md` to previous version
3. Full code rollback: `git revert <merge-commit>` + redeploy
4. Estimated rollback time: 5 minutes (config change) or 15 minutes (code revert)

---

## Future Enhancements (Post Phase 1-2)

These are **NOT** in scope for this plan but inform the architecture:

1. **Function Calling API** (Phase 3.1): Replace JSON extraction with Gemini native function calling
2. **Conversation Memory** (Phase 3.2): Track last 5 turns for context
3. **Parallel Execution** (Phase 4.1): Run independent tool calls concurrently
4. **Semantic Matching** (Phase 4.2): Use embeddings for token intent matching

---

## Files Changed Summary

| File | Changes | Lines Added | Lines Modified | Tests |
|------|---------|-------------|----------------|-------|
| `prompts/planner.md` | Add examples, reasoning instructions | +80 | 0 | Manual |
| `app/planner.py` | Reflection, confidence, CoT | +350 | ~50 | +200 |
| `app/mcp_client.py` | Add `list_tools()` method | +40 | 0 | +30 |
| `app/config.py` | New settings fields | +15 | 0 | +10 |
| `.env.example` | Document new env vars | +5 | 0 | N/A |
| `tests/test_planner.py` | New unit tests | +200 | 0 | N/A |
| `tests/test_planner_integration.py` | New integration tests | +150 | 0 | N/A |
| **TOTAL** | | **~840** | **~50** | **~240** |

---

## Approval Checklist

Before implementation begins:
- [ ] Architecture review: Confirm dataflow changes are sound
- [ ] Performance review: Estimate latency impact (< +200ms acceptable?)
- [ ] Security review: Confirm no secrets in logs or prompts
- [ ] PM approval: Feature flags and rollout plan acceptable?
- [ ] Stakeholder sign-off: Prioritization vs. other roadmap items?

---

## Questions for Review

1. **Confidence threshold**: Is 0.7 the right starting point, or should we be more conservative (0.8)?
2. **Refinement iterations**: Should we allow 3 iterations max instead of 2?
3. **Prompt template**: Should we keep `prompts/planner.md` or migrate to Python string for better version control?
4. **Tool schema caching**: 1 hour TTL reasonable, or should we refresh on every MCP server restart?
5. **Backward compatibility**: Do we need to support old prompt template indefinitely via feature flag?

---

**Status**: Ready for review  
**Estimated effort**: 80-100 developer hours across 3-4 weeks  
**Reviewers**: @backend-lead, @ml-lead, @product-owner  
**Next steps**: Address review comments → Approval → Create implementation branch
