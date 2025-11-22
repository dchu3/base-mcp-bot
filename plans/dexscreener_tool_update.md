# Plan: Update Dexscreener Tool Definitions

## Objective
Review and update the `dexscreener` tool definitions in `GeminiPlanner` (`app/planner.py`) to align with the actual available tools and ensure robust parameter handling.

## Analysis of `DEX_TOKEN_METHODS`
The current `DEX_TOKEN_METHODS` set contains:
- `getPairsByToken` (Valid)
- `getTokenOverview` (❌ Invalid/Missing from standard MCP)
- `searchPairs` (Valid)
- `getPairByAddress` (❌ Invalid - likely `getPairByChainAndAddress`)
- `getPairByChainAndAddress` (Valid)
- `getTokenPools` (Valid)
- `getLatestBoostedTokens` (Valid)
- `getMostActiveBoostedTokens` (Valid)
- `getLatestTokenProfiles` (Valid)

**Findings:**
1.  `getTokenOverview` and `getPairByAddress` appear to be hallucinations or deprecated methods and should be removed.
2.  `checkTokenOrders` is a valid available tool (per system context) but is missing from the codebase entirely. However, since it returns *orders* rather than *token profiles*, it should **not** be added to `DEX_TOKEN_METHODS` (which triggers token card formatting). Instead, it will be supported via parameter normalization so the raw JSON result can be displayed.

## Proposed Changes

### 1. Update `app/planner.py`
-   **Modify `DEX_TOKEN_METHODS`**:
    -   Remove `getTokenOverview`.
    -   Remove `getPairByAddress`.

-   **Update `_normalize_params`**:
    -   Add a specific block for `client == "dexscreener"`.
    -   **Mappings**:
        -   For methods `getPairsByToken`, `getTokenPools`, `checkTokenOrders`:
            -   Map `address`, `token`, `query` -> `tokenAddress`.
        -   For method `getPairByChainAndAddress`:
            -   Map `address`, `pair`, `query` -> `pairAddress`.
    -   **Chain ID Handling**:
        -   Ensure `chainId` is populated from the context `network` if missing.

### 2. Verification
-   The bot should successfully plan calls to `dexscreener.checkTokenOrders` when asked about orders (e.g., "check orders for token X").
-   The bot should continue to handle standard token lookups (`getPairsByToken`) correctly.
-   Removing the invalid methods prevents the planner from hallucinating calls to them.

## File Changes
-   `app/planner.py`

## Next Steps
1.  Approve this plan.
2.  Apply changes to `app/planner.py`.
