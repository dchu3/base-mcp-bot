# Plan: Fix Dexscreener Parameter Normalization

## Objective
Address review comments on PR #24 regarding `dexscreener` parameter normalization in `app/planner.py`.

## Issues Identified
1.  **Undefined `context`**: The code attempts to access `context.get("network", "base")` but `context` is not in the scope. The `network` variable is passed as an argument to `_normalize_params`.
2.  **Duplicate Parameters (Token)**: Mapped aliases (`address`, `token`, `query`) are not removed after being assigned to `tokenAddress`.
3.  **Duplicate Parameters (Pair)**: Mapped aliases (`address`, `pair`, `query`) are not removed after being assigned to `pairAddress`.

## Proposed Changes
-   **File**: `app/planner.py`
-   **Method**: `_normalize_params`

### 1. Fix `chainId` Defaulting
-   Change `normalized["chainId"] = str(context.get("network", "base"))`
-   To `normalized["chainId"] = str(network or "base")`

### 2. Cleanup Token Aliases
-   After setting `normalized["tokenAddress"] = addr`:
    -   `normalized.pop("address", None)`
    -   `normalized.pop("token", None)`
    -   `normalized.pop("query", None)`

### 3. Cleanup Pair Aliases
-   After setting `normalized["pairAddress"] = pair_addr`:
    -   `normalized.pop("address", None)`
    -   `normalized.pop("pair", None)`
    -   `normalized.pop("query", None)`

## Verification
-   Run `pytest` to ensure no regressions.
-   (Implicit) The changes correct a `NameError` and improve parameter hygiene.
