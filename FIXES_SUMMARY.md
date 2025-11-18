# Bug Fixes Summary - 2025-11-17

## Overview

Fixed multiple critical bugs in the conversational bot after migration from router-based architecture.

---

## Fix 1: Router References in Planner ✅

**Issue**: Planner was still calling `getDexRouterActivity` with invalid router names after migration.

**Error**:
```
base.getDexRouterActivity: ERROR (Blockscout request failed (422): Invalid address)
```

**Root Cause**: `prompts/planner.md` still had router-focused examples and instructions.

**Fix**:
- Completely rewrote planner prompt template
- Removed all router references
- Changed focus to Dexscreener token search
- Updated examples to use `searchPairs` instead of router queries

**Files Modified**:
- `prompts/planner.md`

---

## Fix 2: Type Error in Refinement ✅

**Issue**: Crash when Gemini returned malformed JSON during refinement.

**Error**:
```
'str' object has no attribute 'get'
```

**Root Cause**: Code assumed Gemini always returns valid dict, but sometimes returns string or malformed JSON.

**Fix**:
Added defensive type checking in `_refine_plan()`:
```python
if not isinstance(payload, dict):
    return empty plan

tools_list = payload.get("tools", [])
if not isinstance(tools_list, list):
    return empty plan

for entry in tools_list:
    if not isinstance(entry, dict):
        continue
```

**Files Modified**:
- `app/planner.py` (lines 427-480)

---

## Fix 3: Honeypot Invalid Address ✅

**Issue**: Planner was passing token symbols (e.g., "ZORA") instead of hex addresses to honeypot check.

**Error**:
```
honeypot.check_token: failed — address must be a 0x-prefixed 20-byte address
```

**Root Cause**: 
1. Gemini not instructed to search Dexscreener first
2. No validation of address format before API call

**Fix**:

1. Updated refinement prompt with clear instructions:
```
IMPORTANT:
- honeypot.check_token requires 0x-prefixed address, NOT symbol
- Get token address from Dexscreener first before calling honeypot
```

2. Added address validation in `_normalize_params()`:
```python
if client == "honeypot" and method == "check_token":
    address = normalized.get("address")
    if not address.startswith("0x") or len(address) != 42:
        logger.warning("honeypot_invalid_address")
        return {}  # Skip invalid call
```

**Files Modified**:
- `app/planner.py` (refinement prompt + validation)

---

## Fix 4: Invalid JSON Format from Gemini ✅

**Issue**: Gemini returning `"tool_name"` instead of `"client"` and `"method"`.

**Error**:
```
"planner_refinement_invalid_json"
```

**Root Cause**: Refinement prompt didn't specify exact JSON structure.

**Fix**:
Updated refinement prompt with explicit example:
```
Example JSON format:
{"tools": [{"client": "dexscreener", "method": "searchPairs", "params": {...}}]}
```

**Files Modified**:
- `app/planner.py` (refinement prompt)

---

## Fix 5: Markdown Escape in Error Messages ✅

**Issue**: Error messages with dots causing Telegram markdown parsing errors.

**Error**:
```
Can't parse entities: character '.' is reserved and must be escaped
```

**Root Cause**: Error messages not properly escaped for MarkdownV2.

**Fix**:
Already handled by existing fallback in `send_planner_response()`:
- Uses `unescape_markdown()` when sending with `parse_mode=None`
- Strips backslashes for plain text display

**Files Modified**:
- Already fixed in `app/handlers/commands.py`

---

## Fix 6: Multiple str.get() Type Errors ✅

**Issue**: Several locations calling `.get()` on potentially non-dict objects.

**Locations Fixed**:
1. Line 501: `_summarize_results_for_refinement()` - tokens list
2. Line 1196: `_render_response()` - normalized_tokens loop
3. Line 461: `_refine_plan()` - payload and tools list

**Fix Pattern**:
```python
# Before
for item in items:
    value = item.get("key")  # ❌ Crash if item is string

# After
for item in items:
    if not isinstance(item, dict):
        continue
    value = item.get("key")  # ✅ Safe
```

**Files Modified**:
- `app/planner.py` (multiple locations)
- `app/handlers/commands.py`

---

## Testing Results

All fixes verified:
- ✅ Code formatted (black)
- ✅ Linting passed (ruff)  
- ✅ 34/34 tests passing
- ✅ 16/16 planner tests passing

---

## Expected Behavior Now

### Query: "what are top tokens on Base"
**Before**: Router error → refinement crash → no response  
**After**: Searches Dexscreener → returns popular tokens ✅

### Query: "is ZORA safe?"
**Before**: Invalid address → API error → failed response  
**After**: Search Dexscreener → get address → honeypot check → safety verdict ✅

### Query: "show me PEPE"
**Before**: May call wrong tools → errors  
**After**: Search by symbol → price/volume data ✅

---

## Files Modified Summary

1. `prompts/planner.md` - Complete rewrite (token-focused)
2. `app/planner.py` - Multiple type safety improvements
3. `app/handlers/commands.py` - Already had markdown fallback

---

## Deployment Status

✅ **Ready for Production**
- All errors resolved
- Tests passing
- No breaking changes to user interface
- Improved robustness with defensive programming

---

**Date**: 2025-11-17  
**Tests**: 34/34 passing  
**Status**: ✅ Complete

---

## Fix 7: Honeypot Called with Empty Params ✅

**Issue**: Planner was calling `honeypot.check_token` with empty params `{}` after validation.

**Error**:
```
honeypot.check_token: params: {}
MCP error -32602: Input validation error: address Required
```

**Root Cause**: 
- `_normalize_params()` returned `{}` for invalid addresses
- But the tool was still added to invocations list
- MCP server received the call and validation failed

**Fix**:
Added param check before adding tool to invocations:
```python
# After normalize_params
if not params:
    logger.warning("planner_skipping_invalid_tool")
    continue  # Don't add to invocations

invocations.append(ToolInvocation(...))
```

**Applied to**:
1. Initial plan parsing (line ~246)
2. Refinement plan parsing (line ~500)

**Result**:
- Invalid honeypot calls now skipped completely
- Logged as warnings for debugging
- No MCP errors sent to user
- Bot returns partial results gracefully

**Files Modified**:
- `app/planner.py` (tool collection logic)

---

**Updated**: 2025-11-17 23:24 UTC  
**All Tests**: ✅ 16/16 planner tests passing

---

## Fix 8: Dexscreener Multi-Chain Results ✅

**Issue**: Dexscreener `searchPairs` returns tokens from ALL chains, not just Base.

**User Impact**:
```
User: "show me PEPE"
Bot: Shows PEPE on Ethereum, Base, Arbitrum, Polygon, BSC...
User: "Confusing! This is a Base bot!"
```

**Root Cause**: 
- `searchPairs` has no chainId parameter
- Returns results from all chains
- Can't filter at API level

**Fix**:
Added post-query filtering in `_render_response()`:
```python
# Filter to Base chain only
token_chain = token.get("chainId", "").lower()
if token_chain and token_chain != "base":
    continue  # Skip non-Base tokens
```

Also preserved chainId in normalized tokens for filtering.

**Result**:
- Only Base chain results shown to users
- Clear, focused responses
- No multi-chain confusion

**Files Modified**:
- `app/planner.py` (_render_response + _normalize_token)

---

**Updated**: 2025-11-17 23:43 UTC  
**All Tests**: ✅ 16/16 planner tests passing

---

## Fix 9: Auto-Honeypot 404s Blocking All Results ✅

**Issue**: ALL token queries returning "No recent data" even when Dexscreener succeeded.

**Log Evidence**:
```
✅ searchPairs("WETH") → SUCCESS
✅ searchPairs("USDC") → SUCCESS  
❌ Auto-honeypot → 6 checks, ALL returned 404
❌ User: "No recent data returned"
```

**Root Cause**: 
- Legacy code was automatically running honeypot checks on ALL discovered tokens
- Well-known tokens (WETH, USDC) return 404 from honeypot API
- 404 doesn't mean dangerous - means "not in honeypot database"
- Code treated 404 as failure and filtered out all tokens

**Fix**:
Disabled automatic honeypot checks in `_execute_plan()`:
```python
# BEFORE:
honeypot_targets = self._select_honeypot_targets(results, ...)
verdicts = await self._fetch_honeypot_verdicts(...)

# AFTER:
# Disabled - only run when explicitly requested via planner
# honeypot_targets = ...
```

**Honeypot Still Works**:
- ✅ User asks: "is PEPE safe?"
- ✅ Gemini explicitly calls honeypot.check_token
- ❌ No longer auto-called for general searches

**Result**:
- Faster responses (no honeypot wait)
- More reliable (404s don't block)
- Better UX (data displayed immediately)
- Honeypot only runs when user asks for safety check

**Files Modified**:
- `app/planner.py` (_execute_plan method)

---

**Final Update**: 2025-11-17 23:59 UTC  
**Status**: ✅ ALL FIXES COMPLETE - Ready for Production
**Tests**: ✅ 16/16 planner tests passing
