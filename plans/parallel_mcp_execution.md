# Implementation Plan: Parallel MCP Tool Execution

## Context
**Goal:** optimize the Telegram bot's latency by executing independent MCP tool calls in parallel rather than sequentially.
**Current State:** The bot calls Tool A, waits for the result, then calls Tool B.
**Target State:** The bot fires Tool A and Tool B simultaneously using `asyncio.gather`.

---

## Step 1: Update System Prompt

**Target File:** `prompts/planner.md` (or your main system prompt file)

**Instruction:**
Add the following directive to the system prompt to ensure Gemini understands it *can* and *should* output multiple function calls at once.

> **Constraint Checklist & Confidence Score:**
> 1. ...
> 2. **Parallel Execution:** If you require data from multiple independent sources (e.g., getting a token's price AND checking its honeypot status), you MUST generate all necessary tool calls in the same turn. Do not wait for the first result before requesting the second.

---

## Step 2: Refactor Tool Execution Logic

**Target File:** `app/bot.py` (or the file containing the main `chat_loop` and `process_tool_calls`)

**Instruction:**
Locate the loop that iterates through the model's function calls. Refactor it to use `asyncio.gather`.

### Logic Requirements:

1.  **Collect Tasks:** Instead of `await`ing inside the loop, create a list of coroutines (tasks).
2.  **Execute Concurrently:** Use `asyncio.gather(*tasks, return_exceptions=True)` to run them all at once.
3.  **Error Isolation:** If one tool fails (e.g., Dexscreener times out), it must **not** crash the other tools (e.g., Honeypot). The bot should still report the successful data.
4.  **Map Results:** Ensure the results are mapped back to the correct tool call ID so Gemini knows which result belongs to which request.

### Reference Implementation Pattern

Use this Python pattern as the guide for the refactor:

```python
import asyncio
from google.generativeai.types import FunctionResponse

async def execute_parallel_tools(tool_calls, mcp_session):
    """
    Executes a list of Gemini tool calls in parallel.
    
    Args:
        tool_calls: The list of function call objects from the Gemini response part.
        mcp_session: The active MCP client session.
    """
    tasks = []
    
    # 1. Create tasks for all requested tools
    for call in tool_calls:
        # Note: Wrap your existing tool execution logic in a function if it isn't already
        task = asyncio.create_task(execute_single_tool(mcp_session, call))
        tasks.append(task)
    
    # 2. Run all tasks simultaneously
    # return_exceptions=True ensures one failure doesn't crash the whole batch
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 3. Process results for the LLM
    final_responses = []
    for call, result in zip(tool_calls, results):
        if isinstance(result, Exception):
            # Log the error locally
            print(f"Error executing {call.name}: {result}")
            # Return a graceful error to the LLM so it can decide what to do
            func_response = {
                "name": call.name,
                "response": {"error": f"Tool execution failed: {str(result)}"}
            }
        else:
            func_response = {
                "name": call.name,
                "response": result
            }
        final_responses.append(func_response)
        
    return final_responses

async def execute_single_tool(session, call):
    """
    Helper wrapper to standardize tool calling.
    """
    tool_name = call.name
    tool_args = call.args
    # Your existing logic to call the MCP server goes here:
    return await session.call_tool(tool_name, tool_args)
```
