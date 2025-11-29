# Multi-Chain Support Plan (Base + Solana)

**Created:** 2025-11-29  
**Status:** Planned  
**Priority:** Future Enhancement

## Overview

Currently the bot is hardcoded to Base chain. This plan outlines changes needed to support multiple chains, specifically adding Solana alongside Base.

## Current Hardcoded Locations

| File | Line | Hardcoded Value | Purpose |
|------|------|-----------------|---------|
| `simple_planner.py` | 50 | `self.chain_id = "base"` | Default chain for planner |
| `simple_planner.py` | 155, 394, 453 | `chainId: 8453` | Honeypot API (Base EVM chain ID) |
| `simple_planner.py` | 189, 216, 435 | `chainId == "base"` | Filter results to Base chain |
| `intent_matcher.py` | 116 | `network = "base"` | Default network for pool analytics |
| `planner.py` | 2119-2141 | `return "base"`, `return 8453` | Default chain derivation |
| `agents/context.py` | 24 | `network: str = "base"` | Default agent context |
| `agents/discovery.py` | 14-20 | Multiple `"base"` | Chain ID derivation |
| `token_card.py` | 89 | `chainId or "base"` | Default for Dexscreener links |

## Chain Differences

| Feature | Base | Solana |
|---------|------|--------|
| Address format | `0x...` (40 hex chars) | Base58 (32-44 chars) |
| Chain ID | 8453 (EVM) | N/A (not EVM) |
| Honeypot API | ✅ Supported | ❌ Not supported |
| Blockscout | ✅ Works | ❌ Need alternative |
| Dexscreener | ✅ Works | ✅ Works |
| DexPaprika | ✅ Works | ✅ Works |

## Implementation Steps

### Phase 1: Configuration

1. **Add to `.env.example` and `config.py`:**
   ```python
   DEFAULT_CHAIN = "base"  # or "solana"
   SUPPORTED_CHAINS = "base,solana"
   ```

2. **Update `intent_matcher.py`:**
   - Already has `NETWORK_ALIASES` with Solana ✅
   - Add Solana address pattern detection (base58)

### Phase 2: Address Detection

Update `intent_matcher.py` to detect address format:

```python
# Current (EVM only)
ADDRESS_PATTERN = re.compile(r"\b(0x[a-fA-F0-9]{40})\b")

# Add Solana pattern
SOLANA_ADDRESS_PATTERN = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b")
```

### Phase 3: Planner Updates

1. **`simple_planner.py`:**
   - Make `self.chain_id` configurable from settings
   - Skip honeypot checks for non-EVM chains
   - Remove Base-only filtering or make it configurable

2. **`planner.py`:**
   - Update `_derive_chain_id()` to handle Solana
   - Update `_derive_numeric_chain_id()` to return None for non-EVM

### Phase 4: Safety Checks

For Solana, need alternative to Honeypot.is:

| Option | Description |
|--------|-------------|
| [RugCheck](https://rugcheck.xyz/) | Solana token safety API |
| [Birdeye](https://birdeye.so/) | Solana analytics with risk scores |
| Skip safety | Show warning that safety check unavailable |

**Recommendation:** Add RugCheck MCP server for Solana safety checks.

### Phase 5: On-Chain Data

Blockscout only works for EVM chains. For Solana:

| Option | Description |
|--------|-------------|
| [Helius](https://helius.xyz/) | Solana RPC + enhanced APIs |
| [Solscan](https://solscan.io/) | Solana block explorer |
| Skip on-chain | Use Dexscreener/DexPaprika only |

**Recommendation:** Start with Dexscreener/DexPaprika only, add Helius MCP later.

## Code Changes Summary

### `config.py`
```python
default_chain: str = Field(default="base", alias="DEFAULT_CHAIN")
```

### `intent_matcher.py`
```python
def detect_chain_from_address(address: str) -> str:
    if ADDRESS_PATTERN.match(address):
        return "base"  # or other EVM chain
    if SOLANA_ADDRESS_PATTERN.match(address):
        return "solana"
    return "unknown"
```

### `simple_planner.py`
```python
async def _handle_token_lookup(self, matched, context):
    chain = matched.network or self.default_chain
    
    # Skip honeypot for non-EVM
    if chain in ("base", "ethereum", "arbitrum"):
        honeypot_data = await self._check_honeypot(address, chain)
    else:
        honeypot_data = None  # Or use RugCheck for Solana
```

## New MCP Servers Needed

| Server | Purpose | Priority |
|--------|---------|----------|
| rugcheck-mcp | Solana token safety | High |
| helius-mcp | Solana on-chain data | Medium |
| solscan-mcp | Solana explorer | Low |

## Testing Plan

1. Test Base functionality unchanged after refactor
2. Test Solana address detection
3. Test Solana token lookup via Dexscreener
4. Test Solana pools via DexPaprika
5. Test graceful handling when safety check unavailable

## Risks

- Breaking existing Base functionality
- Solana safety checks less reliable than Honeypot
- Increased complexity in intent matching
- User confusion about which chain they're querying

## Timeline Estimate

| Phase | Effort |
|-------|--------|
| Phase 1: Configuration | 1 hour |
| Phase 2: Address Detection | 2 hours |
| Phase 3: Planner Updates | 4 hours |
| Phase 4: Safety Checks | 4+ hours (if adding RugCheck MCP) |
| Phase 5: On-Chain Data | Optional |
| Testing | 2 hours |

**Total:** ~10-15 hours depending on safety check implementation

## References

- [DexPaprika Networks](https://docs.dexpaprika.com/) - 29 chains supported
- [Dexscreener API](https://docs.dexscreener.com/) - Multi-chain support
- [RugCheck API](https://rugcheck.xyz/) - Solana safety
- [Helius Docs](https://docs.helius.xyz/) - Solana RPC
