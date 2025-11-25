# Hierarchical Agent Planner Improvement Plan

## Objective
Improve the current `GeminiPlanner` logic by transitioning to a hierarchical "Agentic" architecture. This will allow for more complex, multi-step reasoning and better coordination of MCP servers (Base, Dexscreener, Honeypot).

## Problem Statement
The current planner (`app/planner.py`) uses a "flat" approach:
1.  It tries to predict all necessary tool calls in a single prompt.
2.  It relies on a simple "refinement" loop that is often insufficient for dependent tasks (e.g., "Find token address" -> "Check safety of that address").
3.  Context sharing between tools is implicit and fragile (relying on the LLM to copy-paste strings correctly in the refinement step).

## Proposed Architecture: The "Agentic" Pattern

We will introduce a **Hierarchical Agent System** coordinated by a top-level **Agentic Coordinator**.

### 1. The Coordinator (Supervisor)
*   **Role**: The "Brain". It interfaces with the user.
*   **Responsibility**:
    *   Understand high-level intent (e.g., "Is PEPE safe?").
    *   Decompose the request into a sequence of sub-tasks.
    *   Delegate tasks to specialized Sub-Agents (Skills).
    *   Synthesize the final response from sub-agent outputs.
*   **State**: Maintains a `ConversationContext` and a `TaskQueue`.

### 2. Sub-Agents (Skills)
These are specialized agents or "Skill Wrappers" that handle specific domains. They expose a simplified interface to the Coordinator.

*   **Discovery Agent (Dexscreener)**
    *   *Tools*: `searchPairs`, `getPairsByToken`, `getLatestBoostedTokens`.
    *   *Goal*: Find token addresses, get price/volume data.
    *   *Input*: Token symbol, name, or query.
    *   *Output*: Structured `TokenInfo` (address, price, liquidity).

*   **Safety Agent (Honeypot)**
    *   *Tools*: `check_token`.
    *   *Goal*: Assess risk.
    *   *Input*: Token address (0x...).
    *   *Output*: `SafetyVerdict` (Safe/Caution/Unsafe, taxes, reasons).

*   **On-Chain Agent (Base)**
    *   *Tools*: `getDexRouterActivity`, `getTransactionByHash`.
    *   *Goal*: Analyze raw chain data.
    *   *Input*: Router address, Transaction Hash.
    *   *Output*: Transaction lists, decoded events.

## Workflow Example: "Is PEPE safe?"

1.  **User**: "Is PEPE safe?"
2.  **Coordinator**: Analyzes request. Identifies need for (1) Token Resolution and (2) Safety Check.
3.  **Coordinator -> Discovery Agent**: "Find address for PEPE".
4.  **Discovery Agent**: Calls `dexscreener.searchPairs("PEPE")`. Returns `0x123...` (PEPE/WETH).
5.  **Coordinator**: Receives `0x123...`.
6.  **Coordinator -> Safety Agent**: "Check safety of 0x123...".
7.  **Safety Agent**: Calls `honeypot.check_token("0x123...")`. Returns "Safe, No Taxes".
8.  **Coordinator**: Synthesizes: "PEPE (0x123...) appears safe with no taxes."

## Implementation Plan

### Phase 1: Core Abstractions
1.  Create `app/agents/base.py`: Define `Agent` interface with `plan()` and `execute()` methods.
2.  Create `app/agents/context.py`: Define `AgentContext` to pass state (found tokens, user intent) between agents.

### Phase 2: Sub-Agent Implementation
1.  Refactor `GeminiPlanner` logic into specialized classes in `app/agents/`:
    *   `DiscoveryAgent`
    *   `SafetyAgent`
    *   `MarketAgent`
2.  Each agent should have its own focused system prompt (e.g., `prompts/agents/discovery.md`).

### Phase 3: Coordinator Implementation
1.  Create `app/agents/coordinator.py`.
2.  Implement the "Reasoning Loop":
    *   **Think**: What do I need?
    *   **Act**: Call Sub-Agent.
    *   **Observe**: Read output.
    *   **Repeat**: Until goal met.

### Phase 4: Integration
1.  Update `app/main.py` to instantiate `CoordinatorAgent` instead of `GeminiPlanner`.
2.  Update `app/planner.py` to serve as the entry point for the new system (or deprecate it).

## Benefits
*   **Robustness**: Dependent steps (Find -> Check) are handled explicitly.
*   **Modularity**: Easier to add new MCP servers (e.g., a "Twitter Agent") without confusing the main planner.
*   **Debuggability**: Easier to trace which sub-agent failed.
