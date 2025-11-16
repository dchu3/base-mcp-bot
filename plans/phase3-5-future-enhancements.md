# Phase 3-5: Future Enhancements for Gemini Planner

**Status**: Future roadmap (not currently scheduled)  
**Created**: 2025-11-16  
**Prerequisites**: Phase 1-2 must be completed and stable  
**Estimated total effort**: 200-300 developer hours

---

## Overview

This document outlines advanced agentic enhancements for the Gemini planner that build on the foundations established in Phase 1-2. These features represent step-function improvements in capability but require more significant architectural changes.

**Implementation Priority**: Only proceed after Phase 1-2 shows measurable success (>20% reduction in failures, stable confidence metrics).

---

## Phase 3: Agentic Enhancements

### 3.1 Function Calling API Migration

**Goal**: Replace text-based JSON extraction with Gemini's native function calling API for 99%+ reliability.

#### Current Pain Points
- JSON parsing failures when Gemini adds commentary
- Brittle code fence stripping logic
- Manual schema synchronization between code and prompts
- No type validation until execution time

#### Proposed Solution

Use Gemini's `FunctionDeclaration` API to define tools as first-class function objects:

```python
from google.generativeai.types import FunctionDeclaration, Tool

class GeminiPlanner:
    def _build_function_declarations(self) -> List[FunctionDeclaration]:
        """Convert MCP tool schemas to Gemini FunctionDeclarations."""
        functions = []
        
        # Base MCP tools
        functions.append(FunctionDeclaration(
            name="base_getDexRouterActivity",
            description="Fetch recent DEX router swap transactions on Base L2",
            parameters={
                "type": "object",
                "properties": {
                    "router": {
                        "type": "string",
                        "description": "Router key (e.g. 'uniswap_v3') or 0x address"
                    },
                    "sinceMinutes": {
                        "type": "integer",
                        "description": "Lookback window in minutes (default 30)"
                    }
                },
                "required": ["router"]
            }
        ))
        
        functions.append(FunctionDeclaration(
            name="dexscreener_searchPairs",
            description="Search for trading pairs by token symbol or name",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Token symbol, name, or pair (e.g. 'PEPE', 'ETH/USDC')"
                    }
                },
                "required": ["query"]
            }
        ))
        
        functions.append(FunctionDeclaration(
            name="honeypot_check_token",
            description="Analyze token for honeypot scams, tax issues, and trading risks",
            parameters={
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "Token contract address"},
                    "chainId": {"type": "integer", "description": "Chain ID (8453 for Base)"},
                    "pair": {"type": "string", "description": "Optional LP pair address"}
                },
                "required": ["address", "chainId"]
            }
        ))
        
        # ... define remaining tools
        return functions
    
    async def _plan_with_functions(
        self,
        message: str,
        context: Dict[str, Any]
    ) -> List[ToolInvocation]:
        """Use Gemini's function calling instead of JSON parsing."""
        tools = [Tool(function_declarations=self._build_function_declarations())]
        
        # Build conversation context
        prompt_context = self._build_context_message(message, context)
        
        response = await asyncio.to_thread(
            self.model.generate_content,
            prompt_context,
            tools=tools,
            tool_config={
                "function_calling_config": {
                    "mode": "ANY",  # Allow multiple function calls
                }
            }
        )
        
        # Extract function calls from structured response
        invocations = []
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'function_call'):
                fc = part.function_call
                invocations.append(self._parse_function_call(fc))
        
        return invocations
    
    def _parse_function_call(self, function_call) -> ToolInvocation:
        """Convert Gemini FunctionCall to ToolInvocation."""
        # Function names are prefixed: "base_getDexRouterActivity"
        parts = function_call.name.split('_', 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid function name: {function_call.name}")
        
        client, method = parts
        params = dict(function_call.args)
        
        return ToolInvocation(
            client=client,
            method=method,
            params=params
        )
```

#### Benefits
- **Eliminates JSON parse failures** - Structured output guaranteed
- **Type validation** - Gemini validates parameters before responding
- **Automatic schema sync** - Can generate from MCP introspection
- **Better multi-turn** - Function results feed back into conversation

#### Migration Strategy
1. Implement `_plan_with_functions()` alongside existing `_plan()`
2. Add feature flag `PLANNER_USE_FUNCTION_CALLING=false`
3. A/B test for 1 week (20% traffic to new API)
4. Compare error rates, latency, tool selection quality
5. Full migration if metrics improve by >30%

#### Estimated Effort
- **Development**: 2 weeks
- **Testing**: 1 week
- **Rollout**: 1 week
- **Total**: ~100 hours

---

### 3.2 Conversation Memory

**Goal**: Enable multi-turn conversations by tracking chat history and maintaining user context.

#### Current State
Each request is stateless - user must repeat context:
```
User: "Check PEPE token"
Bot: [shows PEPE data]
User: "What about liquidity?"  ❌ Bot has no context
```

#### Proposed Solution

```python
@dataclass
class ConversationTurn:
    """Single message exchange in a conversation."""
    timestamp: float
    user_message: str
    bot_response: str
    tokens_mentioned: List[Dict[str, str]]
    tools_called: List[str]

class GeminiPlanner:
    def __init__(self, ...):
        # ... existing init
        self._conversations: Dict[int, List[ConversationTurn]] = {}
        self._conversation_ttl = 3600  # 1 hour
    
    async def run(
        self,
        message: str,
        context: Dict[str, Any],
        chat_id: int
    ) -> PlannerResult:
        """Execute plan with conversation history."""
        # Load recent history (last 5 turns, within TTL)
        history = self._get_conversation_history(chat_id)
        context["conversation_history"] = self._format_history(history)
        
        # Run planning with history
        result = await self._run_with_reflection(message, context)
        
        # Save this turn
        self._save_conversation_turn(
            chat_id,
            ConversationTurn(
                timestamp=time.time(),
                user_message=message,
                bot_response=result.message,
                tokens_mentioned=result.tokens,
                tools_called=[
                    f"{call.client}.{call.method}"
                    for call in plan_payload.tools
                ]
            )
        )
        
        return result
    
    def _get_conversation_history(
        self,
        chat_id: int
    ) -> List[ConversationTurn]:
        """Retrieve recent conversation turns within TTL."""
        all_turns = self._conversations.get(chat_id, [])
        cutoff = time.time() - self._conversation_ttl
        
        # Filter by TTL, keep last 5
        recent = [t for t in all_turns if t.timestamp > cutoff][-5:]
        
        # Clean up old conversations
        if len(recent) < len(all_turns):
            self._conversations[chat_id] = recent
        
        return recent
    
    def _format_history(
        self,
        history: List[ConversationTurn]
    ) -> str:
        """Format conversation history for prompt injection."""
        if not history:
            return "none"
        
        lines = []
        for i, turn in enumerate(history, 1):
            lines.append(f"Turn {i}:")
            lines.append(f"  User: {turn.user_message[:100]}")
            lines.append(f"  Bot: {turn.bot_response[:100]}")
            
            if turn.tokens_mentioned:
                symbols = [t.get("symbol", "?") for t in turn.tokens_mentioned[:3]]
                lines.append(f"  Tokens: {', '.join(symbols)}")
        
        return "\n".join(lines)
```

#### Prompt Changes

Add to `prompts/planner.md`:
```markdown
## Conversation History

Recent conversation turns: $conversation_history

Use this history to:
1. Resolve pronoun references ("it", "that token", "the same pair")
2. Infer implicit context (user asks "liquidity?" after mentioning PEPE → check PEPE liquidity)
3. Avoid redundant lookups (don't re-fetch data from 30 seconds ago)
```

#### Persistence Strategy

**Option A: In-Memory** (Recommended for Phase 3)
- Store in `GeminiPlanner._conversations` dict
- Pros: Simple, fast, no DB changes
- Cons: Lost on restart, not shared across instances
- Use case: Single bot instance, acceptable to lose history on deploy

**Option B: SQLite Persistence**
```python
# app/store/repository.py additions

async def save_conversation_turn(
    self,
    chat_id: int,
    turn: ConversationTurn
) -> None:
    """Persist conversation turn to database."""
    await self.db.execute(
        """
        INSERT INTO conversation_history (chat_id, timestamp, user_message, bot_response, tokens_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (chat_id, turn.timestamp, turn.user_message, turn.bot_response, json.dumps(turn.tokens_mentioned))
    )

async def get_conversation_history(
    self,
    chat_id: int,
    limit: int = 5,
    ttl_seconds: int = 3600
) -> List[ConversationTurn]:
    """Retrieve recent conversation history."""
    cutoff = time.time() - ttl_seconds
    rows = await self.db.fetch_all(
        """
        SELECT timestamp, user_message, bot_response, tokens_json
        FROM conversation_history
        WHERE chat_id = ? AND timestamp > ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (chat_id, cutoff, limit)
    )
    # ... parse and return
```

**Migration**: Create table in `app/store/schema.sql`:
```sql
CREATE TABLE IF NOT EXISTS conversation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    user_message TEXT NOT NULL,
    bot_response TEXT NOT NULL,
    tokens_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_chat_timestamp (chat_id, timestamp)
);
```

#### Success Metrics
- **Context resolution rate**: 80% of follow-up questions correctly reference prior context
- **Redundant lookups**: <5% of requests repeat tool calls from last 60 seconds
- **Memory overhead**: <10MB per 1000 conversations

#### Estimated Effort
- **In-memory version**: 1 week (40 hours)
- **Persistent version**: 2 weeks (80 hours)

---

### 3.3 Confidence Scoring & Clarification (Enhanced)

**Note**: Basic version implemented in Phase 1.4. This section covers advanced enhancements.

#### Advanced Clarification Strategies

**Multi-Choice Clarification**:
```python
@dataclass
class ClarificationChoice:
    """Single option in a multi-choice clarification."""
    label: str
    description: str
    auto_params: Dict[str, Any]

async def _request_clarification(
    self,
    message: str,
    choices: List[ClarificationChoice]
) -> PlannerResult:
    """Present multiple options to user."""
    lines = ["I found multiple possibilities:"]
    
    for i, choice in enumerate(choices, 1):
        lines.append(f"{i}. {choice.label} - {choice.description}")
    
    lines.append("\nReply with the number of your choice.")
    
    return PlannerResult(
        message="\n".join(lines),
        tokens=[],
        pending_clarification={
            "choices": [asdict(c) for c in choices],
            "original_message": message
        }
    )
```

**Smart Disambiguation**:
```python
# When user says "Check PEPE"
if multiple_pepe_tokens_found:
    choices = [
        ClarificationChoice(
            label="PEPE (Base)",
            description="Liquidity: $2.3M, 24h vol: $890K",
            auto_params={"chainId": "base", "address": "0xabc..."}
        ),
        ClarificationChoice(
            label="PEPE (Ethereum)",
            description="Liquidity: $120M, 24h vol: $45M",
            auto_params={"chainId": "ethereum", "address": "0xdef..."}
        )
    ]
    return await self._request_clarification(message, choices)
```

#### Estimated Effort
- **Multi-choice UI**: 1 week (40 hours)
- **Smart disambiguation**: 1 week (40 hours)

---

## Phase 4: Advanced Optimizations

### 4.1 Parallel Tool Execution

**Goal**: Execute independent tool calls concurrently to reduce latency.

#### Current Bottleneck
Sequential execution wastes time:
```
getDexRouterActivity → 2.5s
  getPairsByToken(0xaaa) → 1.8s
  getPairsByToken(0xbbb) → 1.9s
  check_token(0xaaa) → 2.1s
  check_token(0xbbb) → 2.3s
Total: ~10.6s
```

#### Parallelization Strategy

```python
class GeminiPlanner:
    async def _execute_plan(
        self,
        plan: Sequence[ToolInvocation],
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Execute tools with automatic parallelization."""
        # Build dependency graph
        graph = self._build_dependency_graph(plan)
        
        results = []
        executed_ids = set()
        
        # Execute in waves (parallel within wave, sequential across waves)
        while len(executed_ids) < len(plan):
            # Find next batch (all dependencies satisfied)
            ready = [
                call for i, call in enumerate(plan)
                if i not in executed_ids
                and all(dep in executed_ids for dep in graph.get(i, []))
            ]
            
            if not ready:
                break  # Circular dependency or error
            
            # Execute batch in parallel
            batch_results = await asyncio.gather(*[
                self._execute_single_tool(call, context)
                for call in ready
            ], return_exceptions=True)
            
            for call, result in zip(ready, batch_results):
                idx = plan.index(call)
                executed_ids.add(idx)
                
                if isinstance(result, Exception):
                    results.append({"call": call, "error": str(result)})
                else:
                    results.append({"call": call, "result": result})
        
        return results
    
    def _build_dependency_graph(
        self,
        plan: Sequence[ToolInvocation]
    ) -> Dict[int, Set[int]]:
        """Identify which tool calls depend on others."""
        graph = {}
        
        for i, call in enumerate(plan):
            deps = set()
            
            # Router activity must execute before token lookups
            if call.client == "dexscreener":
                for j, prior in enumerate(plan[:i]):
                    if prior.method == "getDexRouterActivity":
                        deps.add(j)
            
            # Honeypot checks should wait for Dexscreener (to get pair)
            if call.client == "honeypot":
                for j, prior in enumerate(plan[:i]):
                    if prior.client == "dexscreener":
                        token_addr = self._extract_token_param(call.params)
                        prior_token = self._extract_token_param(prior.params)
                        if token_addr and token_addr.lower() == prior_token.lower():
                            deps.add(j)
            
            if deps:
                graph[i] = deps
        
        return graph
```

#### Example Execution Plan

```
Wave 1 (parallel):
  - getDexRouterActivity(uniswap_v3)

Wave 2 (parallel):
  - getPairsByToken(0xaaa)  # Discovered from Wave 1
  - getPairsByToken(0xbbb)

Wave 3 (parallel):
  - check_token(0xaaa)  # Can use pair from Wave 2
  - check_token(0xbbb)

Latency: ~6s (vs 10.6s sequential) = 43% improvement
```

#### Configuration

```python
# app/config.py
planner_enable_parallel_execution: bool = Field(
    default=True,
    alias="PLANNER_ENABLE_PARALLEL_EXECUTION"
)
planner_max_parallel_calls: int = Field(
    default=5,
    alias="PLANNER_MAX_PARALLEL_CALLS",
    ge=1,
    le=20
)
```

#### Estimated Effort
- **Dependency graph logic**: 1 week (40 hours)
- **Parallel execution**: 1 week (40 hours)
- **Testing**: 1 week (40 hours)
- **Total**: ~120 hours

---

### 4.2 Semantic Token Matching

**Goal**: Use embeddings to match user queries to watchlist tokens, even with typos or alternate names.

#### Current Limitation
Exact string matching fails:
```
User: "How's that pepe coin doing?"
Watchlist: [{"symbol": "PEPE", "name": "Pepe Token"}]
Result: ❌ No match (case mismatch, "coin" not in data)
```

#### Embeddings Approach

```python
from google.generativeai import embed_content

class GeminiPlanner:
    def __init__(self, ...):
        # ... existing init
        self._embedding_cache: Dict[str, List[float]] = {}
        self._cache_ttl = 3600
    
    async def _enrich_context_with_semantic_matches(
        self,
        context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Add semantically similar tokens from watchlist."""
        user_query = context.get("message", "")
        watchlist = context.get("recent_tokens", [])
        
        if not watchlist or not user_query:
            return context
        
        # Generate embeddings
        query_emb = await self._get_embedding(user_query)
        
        matches = []
        for token in watchlist:
            # Build searchable text
            token_text = f"{token.get('symbol', '')} {token.get('name', '')}"
            token_emb = await self._get_embedding(token_text)
            
            similarity = self._cosine_similarity(query_emb, token_emb)
            if similarity > 0.6:  # Threshold for relevance
                matches.append({
                    "token": token,
                    "similarity": similarity
                })
        
        # Sort by similarity
        matches.sort(key=lambda x: x["similarity"], reverse=True)
        
        context["semantic_token_matches"] = [
            m["token"] for m in matches[:3]
        ]
        
        return context
    
    async def _get_embedding(self, text: str) -> List[float]:
        """Get cached or fresh embedding for text."""
        cache_key = text.lower().strip()
        
        # Check cache
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]
        
        # Generate new embedding
        result = await asyncio.to_thread(
            embed_content,
            model="models/embedding-001",
            content=text,
            task_type="retrieval_query"
        )
        
        embedding = result["embedding"]
        self._embedding_cache[cache_key] = embedding
        
        return embedding
    
    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        import math
        
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        
        if mag_a == 0 or mag_b == 0:
            return 0.0
        
        return dot / (mag_a * mag_b)
```

#### Prompt Integration

Add to `prompts/planner.md`:
```markdown
## Semantic Token Matches

Tokens from watchlist that semantically match the user's query: $semantic_token_matches

These are high-confidence matches even if symbols don't exactly match the query.
Prefer using these over searching Dexscreener.
```

#### Benefits
- **Typo tolerance**: "peep" matches "PEPE"
- **Alternate names**: "dogwifhat" matches "WIF"
- **Multilingual**: "Bitcoin" matches "BTC"
- **Context awareness**: "that meme coin" matches recent PEPE mention

#### Estimated Effort
- **Embedding integration**: 1 week (40 hours)
- **Caching & optimization**: 1 week (40 hours)
- **Total**: ~80 hours

---

### 4.3 Streaming Partial Results

**Goal**: Send intermediate results to Telegram as they complete, reducing perceived latency.

#### Current UX
User waits 8-10 seconds with no feedback, then gets full response.

#### Streaming UX

```
User: "What's moving on Base?"

[Immediately]
Bot: "Checking Uniswap V3 router..."

[2 seconds later]
Bot: "Found 12 recent swaps. Analyzing tokens..."

[4 seconds later]
Bot: "🟢 PEPE - $0.000012 (+15.3%)
     Liquidity: $2.1M | Volume: $890K
     [Dexscreener link]
     Checking for honeypot risks..."

[6 seconds later]
Bot: "✅ SAFE_TO_TRADE - No major risks detected"
```

#### Implementation

```python
from typing import AsyncGenerator

class GeminiPlanner:
    async def run_streaming(
        self,
        message: str,
        context: Dict[str, Any],
        chat_id: int
    ) -> AsyncGenerator[str, None]:
        """Stream partial results as tools complete."""
        yield "🔍 Planning your request..."
        
        plan_payload = await self._plan(message, context)
        
        if plan_payload.confidence < self.CONFIDENCE_THRESHOLD:
            yield plan_payload.clarification or "Could you clarify?"
            return
        
        # Stream tool execution
        for i, call in enumerate(plan_payload.tools, 1):
            status = f"⏳ Executing {call.client}.{call.method} ({i}/{len(plan_payload.tools)})..."
            yield status
            
            try:
                result = await self._execute_single_tool(call, context)
                
                # Stream partial formatted result
                if call.client == "dexscreener":
                    tokens = self._extract_token_entries(result)
                    for token in tokens[:3]:  # Stream first 3
                        yield format_token_summary(token)
                
            except Exception as exc:
                yield f"❌ {call.method} failed: {str(exc)[:100]}"
        
        yield "✅ Complete!"

# app/handlers/planner.py integration
async def handle_planner_streaming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for streaming planner responses."""
    message = update.message
    sent_message = None
    accumulated = []
    
    async for chunk in planner.run_streaming(message.text, ctx, message.chat_id):
        accumulated.append(chunk)
        
        if sent_message is None:
            # Send first message
            sent_message = await message.reply_text(chunk, parse_mode="Markdown")
        else:
            # Edit with accumulated content
            try:
                await sent_message.edit_text(
                    "\n\n".join(accumulated[-10:]),  # Last 10 chunks to avoid msg size limit
                    parse_mode="Markdown"
                )
            except telegram.error.BadRequest:
                # Message unchanged, skip edit
                pass
        
        await asyncio.sleep(0.5)  # Rate limit edits
```

#### Configuration

```python
# app/config.py
planner_enable_streaming: bool = Field(
    default=False,  # Opt-in initially
    alias="PLANNER_ENABLE_STREAMING"
)
```

#### Challenges
- **Telegram rate limits**: Max 1 edit/second per message
- **Message size limits**: 4096 chars, must truncate
- **Error handling**: How to rollback streamed content?

#### Estimated Effort
- **Streaming infrastructure**: 2 weeks (80 hours)
- **Telegram integration**: 1 week (40 hours)
- **Total**: ~120 hours

---

## Phase 5: Observability & Tuning

### 5.1 Prompt Performance Tracking

**Goal**: Measure and optimize prompt effectiveness over time.

```python
@dataclass
class PromptMetrics:
    """Performance metrics for a prompt variant."""
    total_calls: int = 0
    json_parse_failures: int = 0
    avg_confidence: float = 0.0
    avg_latency_ms: float = 0.0
    tool_selection_accuracy: float = 0.0  # Requires manual labeling
    refinement_rate: float = 0.0
    
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return 1.0 - (self.json_parse_failures / self.total_calls)

class GeminiPlanner:
    def __init__(self, ...):
        # ... existing init
        self._metrics: Dict[str, PromptMetrics] = defaultdict(PromptMetrics)
    
    async def _plan(self, message: str, context: Dict[str, Any]) -> PlanPayload:
        variant = context.get("prompt_variant", "default")
        start = time.time()
        
        try:
            # ... existing planning logic
            result = await self._plan_internal(message, context, variant)
            
            # Track success
            latency_ms = (time.time() - start) * 1000
            self._metrics[variant].total_calls += 1
            self._metrics[variant].avg_latency_ms = (
                self._metrics[variant].avg_latency_ms * 0.9 + latency_ms * 0.1
            )
            self._metrics[variant].avg_confidence = (
                self._metrics[variant].avg_confidence * 0.9 + result.confidence * 0.1
            )
            
            return result
            
        except json.JSONDecodeError:
            self._metrics[variant].json_parse_failures += 1
            raise
    
    def get_metrics_summary(self) -> Dict[str, Dict[str, float]]:
        """Export metrics for monitoring dashboard."""
        return {
            variant: {
                "success_rate": metrics.success_rate(),
                "avg_confidence": metrics.avg_confidence,
                "avg_latency_ms": metrics.avg_latency_ms,
                "total_calls": metrics.total_calls
            }
            for variant, metrics in self._metrics.items()
        }
```

#### Dashboard Integration

```python
# app/handlers/admin.py
async def handle_metrics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to view planner metrics."""
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    
    metrics = planner.get_metrics_summary()
    
    lines = ["📊 Planner Metrics\n"]
    for variant, data in metrics.items():
        lines.append(f"**{variant}**:")
        lines.append(f"  Success: {data['success_rate']:.1%}")
        lines.append(f"  Confidence: {data['avg_confidence']:.2f}")
        lines.append(f"  Latency: {data['avg_latency_ms']:.0f}ms")
        lines.append(f"  Calls: {data['total_calls']}\n")
    
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
```

#### Estimated Effort
- **Metrics tracking**: 1 week (40 hours)
- **Dashboard integration**: 1 week (40 hours)

---

### 5.2 A/B Testing Framework

**Goal**: Compare multiple prompt variants to optimize performance.

```python
class GeminiPlanner:
    PROMPT_VARIANTS = {
        "v1_baseline": Template("""..."""),
        "v2_cot": Template("""..."""),  # With chain-of-thought
        "v3_fewshot": Template("""..."""),  # With examples
        "v4_structured": Template("""...""")  # Structured format
    }
    
    def _select_prompt_variant(self, user_id: int) -> str:
        """Deterministic variant selection based on user ID."""
        # Consistent hash for user
        hash_val = hash(f"prompt_ab_{user_id}") % 100
        
        # Distribution: 40% baseline, 20% each for variants
        if hash_val < 40:
            return "v1_baseline"
        elif hash_val < 60:
            return "v2_cot"
        elif hash_val < 80:
            return "v3_fewshot"
        else:
            return "v4_structured"
    
    async def _plan(self, message: str, context: Dict[str, Any]) -> PlanPayload:
        user_id = context.get("user_id", 0)
        variant = self._select_prompt_variant(user_id)
        
        context["prompt_variant"] = variant
        prompt_template = self.PROMPT_VARIANTS.get(variant, self.DEFAULT_PROMPT)
        
        # ... rest of planning with variant-specific template
```

#### Analysis Tools

```python
def analyze_ab_test_results(metrics: Dict[str, PromptMetrics]) -> str:
    """Statistical analysis of variant performance."""
    baseline = metrics.get("v1_baseline")
    if not baseline or baseline.total_calls < 100:
        return "Insufficient baseline data"
    
    report = ["A/B Test Results\n"]
    report.append(f"Baseline (v1): {baseline.success_rate():.1%} success, n={baseline.total_calls}\n")
    
    for variant, data in metrics.items():
        if variant == "v1_baseline" or data.total_calls < 100:
            continue
        
        # Simple comparison (use proper statistics in production)
        improvement = data.success_rate() - baseline.success_rate()
        confidence_delta = data.avg_confidence - baseline.avg_confidence
        
        report.append(f"{variant}:")
        report.append(f"  Success: {data.success_rate():.1%} ({improvement:+.1%})")
        report.append(f"  Confidence: {data.avg_confidence:.2f} ({confidence_delta:+.2f})")
        report.append(f"  n={data.total_calls}\n")
    
    return "\n".join(report)
```

#### Estimated Effort
- **A/B framework**: 1 week (40 hours)
- **Analysis tools**: 1 week (40 hours)

---

## Summary: Effort & Timeline

| Phase | Component | Effort (hours) | Dependencies |
|-------|-----------|----------------|--------------|
| **3.1** | Function Calling API | 100 | Phase 1-2 complete |
| **3.2** | Conversation Memory | 40-80 | None |
| **3.3** | Enhanced Clarification | 80 | Phase 1.4 |
| **4.1** | Parallel Execution | 120 | Phase 2.1 |
| **4.2** | Semantic Matching | 80 | None |
| **4.3** | Streaming Results | 120 | None |
| **5.1** | Performance Tracking | 80 | None |
| **5.2** | A/B Testing | 80 | 5.1 |
| **TOTAL** | | **760-800 hours** | ~4-5 months |

---

## Recommended Sequencing

### Quarter 1 (Post Phase 1-2)
1. **Conversation Memory (3.2)** - High user value, moderate effort
2. **Performance Tracking (5.1)** - Needed to measure other improvements

### Quarter 2
3. **Function Calling API (3.1)** - Major stability upgrade
4. **Parallel Execution (4.1)** - 40% latency reduction

### Quarter 3
5. **Semantic Matching (4.2)** - Better UX, moderate complexity
6. **A/B Testing (5.2)** - Optimization infrastructure

### Quarter 4
7. **Streaming Results (4.3)** - Nice-to-have UX polish
8. **Enhanced Clarification (3.3)** - Refinement of existing feature

---

## Decision Gates

Before starting each phase, confirm:
- [ ] Phase 1-2 metrics show >20% improvement
- [ ] No regressions in core functionality
- [ ] User feedback is positive (manual review of 50 conversations)
- [ ] Resource availability (dev time, API quotas)

---

**Questions? Updates?**  
Contact: @tech-lead  
Last reviewed: 2025-11-16
