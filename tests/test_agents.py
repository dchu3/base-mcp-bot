"""Tests for the hierarchical agent system."""

import pytest
from unittest.mock import AsyncMock, MagicMock

import google.generativeai as genai

from app.agents.context import AgentContext
from app.agents.base import BaseAgent
from app.agents.coordinator import CoordinatorAgent


@pytest.fixture
def mock_mcp():
    """Create a mocked MCP manager."""
    mcp = MagicMock()
    mcp.base = MagicMock()
    mcp.dexscreener = MagicMock()
    mcp.honeypot = MagicMock()
    return mcp


@pytest.fixture
def mock_coordinator(mock_mcp):
    """Create a coordinator with mocked dependencies."""
    genai.configure = MagicMock()
    return CoordinatorAgent(
        api_key="fake-key",
        mcp_manager=mock_mcp,
        model_name="gemini-1.5-flash",
        router_map={"uniswap_v2": {"base-mainnet": "0x1234"}},
    )


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
        ctx.add_tokens(
            [
                {"address": "0xAAA"},
                {"tokenAddress": "0xBBB"},
            ]
        )

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

    def test_summarize_router_activity(self, mock_coordinator) -> None:
        """Test summarizing router activity results."""
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

        summary = mock_coordinator._summarize_results(results)
        assert "3 transactions found" in summary
        assert "swap" in summary

    def test_summarize_dexscreener_pairs(self, mock_coordinator) -> None:
        """Test summarizing Dexscreener pair results."""
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

        summary = mock_coordinator._summarize_results(results)
        assert "2 pairs found" in summary
        assert "PEPE" in summary

    def test_summarize_honeypot_check(self, mock_coordinator) -> None:
        """Test summarizing honeypot check results."""
        results = [
            {
                "call": {"client": "honeypot", "method": "check_token"},
                "result": {"summary": {"verdict": "SAFE_TO_TRADE"}},
            }
        ]

        summary = mock_coordinator._summarize_results(results)
        assert "Verdict: SAFE_TO_TRADE" in summary

    def test_summarize_error_result(self, mock_coordinator) -> None:
        """Test summarizing error results."""
        results = [
            {
                "call": {"client": "base", "method": "getDexRouterActivity"},
                "error": "Connection timeout",
            }
        ]

        summary = mock_coordinator._summarize_results(results)
        assert "Error" in summary
        assert "Connection timeout" in summary

    def test_summarize_empty_results(self, mock_coordinator) -> None:
        """Test summarizing empty results."""
        summary = mock_coordinator._summarize_results([])
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

    @pytest.mark.asyncio
    async def test_run_finishes_with_response(self, mock_coordinator) -> None:
        """Test that run() returns a PlannerResult when LLM says FINISH."""
        mock_coordinator._generate_content = AsyncMock(
            return_value='{"reasoning": "Done", "next_agent": "FINISH", "final_response": "Here is your answer."}'
        )

        result = await mock_coordinator.run("test message", {})

        assert result.message is not None
        assert "Here is your answer" in result.message

    @pytest.mark.asyncio
    async def test_run_delegates_to_agent(self, mock_coordinator) -> None:
        """Test that run() delegates to the correct sub-agent."""
        call_count = 0

        async def mock_generate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return '{"reasoning": "Need discovery", "next_agent": "discovery"}'
            return '{"reasoning": "Done", "next_agent": "FINISH", "final_response": "Found tokens."}'

        mock_coordinator._generate_content = mock_generate
        mock_coordinator.agents["discovery"].run = AsyncMock(
            return_value={"output": "Found 2 tokens", "data": []}
        )

        result = await mock_coordinator.run("find PEPE", {})

        mock_coordinator.agents["discovery"].run.assert_called_once()
        assert "Found tokens" in result.message
