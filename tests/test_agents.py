"""Tests for the hierarchical agent system."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.agents.context import AgentContext
from app.agents.base import BaseAgent
from app.agents.coordinator import CoordinatorAgent


class TestAgentContext:
    """Tests for AgentContext."""

    def test_add_tokens_deduplication(self) -> None:
        """Test that add_tokens deduplicates by address."""
        ctx = AgentContext(message="test")

        tokens1 = [
            {"address": "0xAAA", "symbol": "TOKEN1"},
            {"address": "0xBBB", "symbol": "TOKEN2"},
        ]
        tokens2 = [
            {
                "address": "0xaaa",
                "symbol": "TOKEN1_DUP",
            },  # Same address, different case
            {"address": "0xCCC", "symbol": "TOKEN3"},
        ]

        ctx.add_tokens(tokens1)
        ctx.add_tokens(tokens2)

        assert len(ctx.found_tokens) == 3
        addresses = [t["address"] for t in ctx.found_tokens]
        assert "0xAAA" in addresses
        assert "0xBBB" in addresses
        assert "0xCCC" in addresses

    def test_add_tokens_handles_tokenAddress_field(self) -> None:
        """Test deduplication works with tokenAddress field."""
        ctx = AgentContext(message="test")

        ctx.add_tokens([{"tokenAddress": "0xAAA", "symbol": "T1"}])
        ctx.add_tokens([{"address": "0xaaa", "symbol": "T1_DUP"}])

        assert len(ctx.found_tokens) == 1

    def test_get_recent_token_addresses(self) -> None:
        """Test extracting addresses from tokens."""
        ctx = AgentContext(message="test")
        # Use add_tokens to properly populate (which deduplicates)
        ctx.add_tokens([
            {"address": "0xAAA"},
            {"tokenAddress": "0xBBB"},
        ])

        addresses = ctx.get_recent_token_addresses()
        assert len(addresses) == 2
        assert "0xAAA" in addresses
        assert "0xBBB" in addresses


class TestBaseAgentParseJson:
    """Tests for BaseAgent._parse_json."""

    def _make_agent(self) -> BaseAgent:
        """Create a concrete agent for testing."""

        class ConcreteAgent(BaseAgent):
            async def run(self, context):
                return {}

        model = MagicMock()
        mcp = MagicMock()
        return ConcreteAgent("test", model, mcp)

    def test_parse_json_plain(self) -> None:
        """Test parsing plain JSON."""
        agent = self._make_agent()
        result = agent._parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_with_markdown_code_block(self) -> None:
        """Test parsing JSON wrapped in markdown code block."""
        agent = self._make_agent()
        result = agent._parse_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_parse_json_with_trailing_whitespace(self) -> None:
        """Test parsing JSON with trailing whitespace after code block."""
        agent = self._make_agent()
        result = agent._parse_json('```json\n{"key": "value"}\n```\n\n')
        assert result == {"key": "value"}

    def test_parse_json_with_leading_whitespace(self) -> None:
        """Test parsing JSON with leading whitespace."""
        agent = self._make_agent()
        result = agent._parse_json('  \n```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}


class TestCoordinatorSummarizeResults:
    """Tests for CoordinatorAgent._summarize_results."""

    def _make_coordinator(self) -> CoordinatorAgent:
        """Create coordinator with mocked dependencies."""
        # We need to mock genai to avoid API key requirement
        import google.generativeai as genai

        genai.configure = MagicMock()

        mcp = MagicMock()
        mcp.base = MagicMock()
        mcp.dexscreener = MagicMock()
        mcp.honeypot = MagicMock()

        return CoordinatorAgent(
            api_key="fake-key",
            mcp_manager=mcp,
            model_name="gemini-1.5-flash",
            router_map={},
        )

    def test_summarize_router_activity(self) -> None:
        """Test summarizing router activity results."""
        coordinator = self._make_coordinator()

        results = [
            {
                "call": {"client": "base", "method": "getDexRouterActivity"},
                "result": {
                    "items": [
                        {"method": "swap"},
                        {"method": "addLiquidity"},
                        {"function": "removeLiquidity"},
                    ]
                },
            }
        ]

        summary = coordinator._summarize_results(results)
        assert "3 transactions found" in summary
        assert "swap" in summary

    def test_summarize_dexscreener_pairs(self) -> None:
        """Test summarizing Dexscreener pair results."""
        coordinator = self._make_coordinator()

        results = [
            {
                "call": {"client": "dexscreener", "method": "searchPairs"},
                "result": {
                    "pairs": [
                        {"baseToken": {"symbol": "PEPE"}},
                        {"baseToken": {"symbol": "DOGE"}},
                    ]
                },
            }
        ]

        summary = coordinator._summarize_results(results)
        assert "2 pairs found" in summary
        assert "PEPE" in summary

    def test_summarize_honeypot_check(self) -> None:
        """Test summarizing honeypot check results."""
        coordinator = self._make_coordinator()

        results = [
            {
                "call": {"client": "honeypot", "method": "check_token"},
                "result": {"summary": {"verdict": "SAFE_TO_TRADE"}},
            }
        ]

        summary = coordinator._summarize_results(results)
        assert "Verdict: SAFE_TO_TRADE" in summary

    def test_summarize_error_result(self) -> None:
        """Test summarizing error results."""
        coordinator = self._make_coordinator()

        results = [
            {
                "call": {"client": "base", "method": "getDexRouterActivity"},
                "error": "Connection timeout",
            }
        ]

        summary = coordinator._summarize_results(results)
        assert "Error" in summary
        assert "Connection timeout" in summary

    def test_summarize_empty_results(self) -> None:
        """Test summarizing empty results."""
        coordinator = self._make_coordinator()

        summary = coordinator._summarize_results([])
        assert summary == "No results yet"


class TestParseJsonUtility:
    """Tests for the shared parse_llm_json utility."""

    def test_parse_plain_json(self) -> None:
        """Test parsing plain JSON."""
        from app.utils.json_utils import parse_llm_json

        result = parse_llm_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_with_code_block(self) -> None:
        """Test parsing JSON with markdown code block."""
        from app.utils.json_utils import parse_llm_json

        result = parse_llm_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_parse_json_with_extra_whitespace(self) -> None:
        """Test parsing JSON with extra whitespace."""
        from app.utils.json_utils import parse_llm_json

        result = parse_llm_json('  \n```json\n{"key": "value"}\n```\n\n  ')
        assert result == {"key": "value"}


class TestCoordinatorIntegration:
    """Integration tests for CoordinatorAgent.run()."""

    def _make_coordinator(self) -> CoordinatorAgent:
        """Create coordinator with mocked dependencies."""
        import google.generativeai as genai

        genai.configure = MagicMock()

        mcp = MagicMock()
        mcp.base = MagicMock()
        mcp.dexscreener = MagicMock()
        mcp.honeypot = MagicMock()

        return CoordinatorAgent(
            api_key="fake-key",
            mcp_manager=mcp,
            model_name="gemini-1.5-flash",
            router_map={"uniswap_v2": {"base-mainnet": "0x1234"}},
        )

    @pytest.mark.asyncio
    async def test_run_finishes_with_response(self) -> None:
        """Test that run() returns a PlannerResult when LLM says FINISH."""
        coordinator = self._make_coordinator()

        # Mock the LLM to immediately return FINISH
        coordinator._generate_content = AsyncMock(
            return_value='{"reasoning": "Done", "next_agent": "FINISH", "final_response": "Here is your answer."}'
        )

        result = await coordinator.run("test message", {})

        assert result.message is not None
        assert "Here is your answer" in result.message

    @pytest.mark.asyncio
    async def test_run_delegates_to_agent(self) -> None:
        """Test that run() delegates to the correct sub-agent."""
        coordinator = self._make_coordinator()

        call_count = 0

        async def mock_generate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return '{"reasoning": "Need discovery", "next_agent": "discovery"}'
            return '{"reasoning": "Done", "next_agent": "FINISH", "final_response": "Found tokens."}'

        coordinator._generate_content = mock_generate
        coordinator.agents["discovery"].run = AsyncMock(
            return_value={"output": "Found 2 tokens", "data": []}
        )

        result = await coordinator.run("find PEPE", {})

        coordinator.agents["discovery"].run.assert_called_once()
        assert "Found tokens" in result.message
