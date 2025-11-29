"""Tests for the agentic planner."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from app.agentic_planner import (
    AgenticPlanner,
    AgenticContext,
    ToolCall,
    AGENTIC_SYSTEM_PROMPT,
)
from app.tool_converter import (
    mcp_type_to_gemini_type,
    convert_json_schema_to_gemini_schema,
    mcp_tool_to_gemini_function,
    convert_mcp_tools_to_gemini,
    parse_function_call_name,
)


class TestToolConverter:
    """Tests for MCP to Gemini tool conversion."""

    def test_mcp_type_to_gemini_type_string(self) -> None:
        """Test string type conversion."""
        import google.generativeai as genai

        result = mcp_type_to_gemini_type("string")
        assert result == genai.protos.Type.STRING

    def test_mcp_type_to_gemini_type_integer(self) -> None:
        """Test integer type conversion."""
        import google.generativeai as genai

        result = mcp_type_to_gemini_type("integer")
        assert result == genai.protos.Type.INTEGER

    def test_mcp_type_to_gemini_type_boolean(self) -> None:
        """Test boolean type conversion."""
        import google.generativeai as genai

        result = mcp_type_to_gemini_type("boolean")
        assert result == genai.protos.Type.BOOLEAN

    def test_mcp_type_to_gemini_type_unknown(self) -> None:
        """Test unknown type defaults to string."""
        import google.generativeai as genai

        result = mcp_type_to_gemini_type("unknown_type")
        assert result == genai.protos.Type.STRING

    def test_convert_simple_schema(self) -> None:
        """Test converting a simple string schema."""
        import google.generativeai as genai

        schema = {"type": "string", "description": "A test string"}
        result = convert_json_schema_to_gemini_schema(schema)
        assert result.type == genai.protos.Type.STRING

    def test_convert_object_schema(self) -> None:
        """Test converting an object schema with properties."""
        import google.generativeai as genai

        schema = {
            "type": "object",
            "properties": {
                "network": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["network"],
        }
        result = convert_json_schema_to_gemini_schema(schema)
        assert result.type == genai.protos.Type.OBJECT
        assert "network" in result.properties
        assert "limit" in result.properties

    def test_convert_array_schema(self) -> None:
        """Test converting an array schema."""
        import google.generativeai as genai

        schema = {
            "type": "array",
            "items": {"type": "string"},
        }
        result = convert_json_schema_to_gemini_schema(schema)
        assert result.type == genai.protos.Type.ARRAY

    def test_mcp_tool_to_gemini_function(self) -> None:
        """Test converting an MCP tool to Gemini function."""
        tool = {
            "name": "getNetworkPools",
            "description": "Get pools on a network",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "network": {"type": "string"},
                },
                "required": ["network"],
            },
        }
        result = mcp_tool_to_gemini_function("dexpaprika", tool)
        assert result is not None
        assert result.name == "dexpaprika_getNetworkPools"
        assert "Get pools" in result.description

    def test_mcp_tool_to_gemini_function_no_params(self) -> None:
        """Test converting a tool with no parameters."""
        tool = {
            "name": "getStats",
            "description": "Get global stats",
        }
        result = mcp_tool_to_gemini_function("dexpaprika", tool)
        assert result is not None
        assert result.name == "dexpaprika_getStats"

    def test_mcp_tool_to_gemini_function_no_name(self) -> None:
        """Test that tools without names return None."""
        tool = {"description": "No name tool"}
        result = mcp_tool_to_gemini_function("test", tool)
        assert result is None

    def test_convert_mcp_tools_to_gemini(self) -> None:
        """Test batch conversion of tools."""
        tools = [
            {"name": "tool1", "description": "First tool"},
            {"name": "tool2", "description": "Second tool"},
        ]
        result = convert_mcp_tools_to_gemini("client", tools)
        assert len(result) == 2
        assert result[0].name == "client_tool1"
        assert result[1].name == "client_tool2"

    def test_parse_function_call_name(self) -> None:
        """Test parsing namespaced function names."""
        client, method = parse_function_call_name("dexpaprika_getNetworkPools")
        assert client == "dexpaprika"
        assert method == "getNetworkPools"

    def test_parse_function_call_name_no_underscore(self) -> None:
        """Test parsing function name without underscore."""
        client, method = parse_function_call_name("someMethod")
        assert client == ""
        assert method == "someMethod"


class TestAgenticContext:
    """Tests for AgenticContext dataclass."""

    def test_default_values(self) -> None:
        """Test default context values."""
        ctx = AgenticContext()
        assert ctx.iteration == 0
        assert ctx.total_tool_calls == 0
        assert ctx.tool_calls == []
        assert ctx.tokens_found == []

    def test_tool_call_tracking(self) -> None:
        """Test tracking tool calls in context."""
        ctx = AgenticContext()
        ctx.tool_calls.append(ToolCall(client="test", method="method", params={}))
        ctx.total_tool_calls += 1
        assert len(ctx.tool_calls) == 1
        assert ctx.total_tool_calls == 1


class TestToolCall:
    """Tests for ToolCall dataclass."""

    def test_tool_call_creation(self) -> None:
        """Test creating a tool call."""
        tc = ToolCall(
            client="dexpaprika",
            method="getNetworkPools",
            params={"network": "base"},
        )
        assert tc.client == "dexpaprika"
        assert tc.method == "getNetworkPools"
        assert tc.params == {"network": "base"}
        assert tc.result is None
        assert tc.error is None

    def test_tool_call_with_result(self) -> None:
        """Test tool call with result."""
        tc = ToolCall(
            client="test",
            method="test",
            params={},
            result={"data": "test"},
        )
        assert tc.result == {"data": "test"}


class TestAgenticPlanner:
    """Tests for AgenticPlanner."""

    def test_planner_initialization(self) -> None:
        """Test planner initialization."""
        mock_mcp = MagicMock()
        mock_mcp.get_gemini_functions = MagicMock(return_value=[])

        planner = AgenticPlanner(
            api_key="test-key",
            mcp_manager=mock_mcp,
            model_name="gemini-1.5-flash",
        )

        assert planner.max_iterations == 5
        assert planner.max_tool_calls == 20
        assert planner.timeout_seconds == 60

    def test_planner_custom_settings(self) -> None:
        """Test planner with custom settings."""
        mock_mcp = MagicMock()

        planner = AgenticPlanner(
            api_key="test-key",
            mcp_manager=mock_mcp,
            max_iterations=3,
            max_tool_calls=10,
            timeout_seconds=30,
        )

        assert planner.max_iterations == 3
        assert planner.max_tool_calls == 10
        assert planner.timeout_seconds == 30

    def test_truncate_result_dict_with_pools(self) -> None:
        """Test truncating dict with pools list."""
        mock_mcp = MagicMock()
        planner = AgenticPlanner(api_key="test", mcp_manager=mock_mcp)

        result = {
            "pools": [{"id": i} for i in range(20)],
        }

        truncated = planner._truncate_result(result, max_items=5)
        assert len(truncated["pools"]) == 5
        assert truncated.get("_pools_truncated") is True

    def test_truncate_result_list(self) -> None:
        """Test truncating a plain list."""
        mock_mcp = MagicMock()
        planner = AgenticPlanner(api_key="test", mcp_manager=mock_mcp)

        result = list(range(20))
        truncated = planner._truncate_result(result, max_items=5)
        assert len(truncated) == 5

    def test_extract_tokens_from_pools(self) -> None:
        """Test extracting tokens from pool results."""
        mock_mcp = MagicMock()
        planner = AgenticPlanner(api_key="test", mcp_manager=mock_mcp)
        ctx = AgenticContext()

        result = {
            "pools": [
                {
                    "chain": "base",
                    "tokens": [
                        {"id": "0x123", "symbol": "TEST", "name": "Test Token"},
                        {"id": "0x456", "symbol": "WETH", "name": "Wrapped Ether"},
                    ],
                }
            ]
        }

        planner._extract_tokens_from_result(result, ctx)

        assert len(ctx.tokens_found) == 2
        assert ctx.tokens_found[0]["symbol"] == "TEST"
        assert ctx.tokens_found[1]["symbol"] == "WETH"

    def test_build_initial_messages(self) -> None:
        """Test building initial message list."""
        mock_mcp = MagicMock()
        planner = AgenticPlanner(api_key="test", mcp_manager=mock_mcp)

        context = {
            "recent_tokens": [
                {"symbol": "PEPE", "address": "0x1234567890abcdef"},
            ],
        }

        messages = planner._build_initial_messages("Check PEPE", context)

        assert len(messages) >= 1
        assert messages[-1]["role"] == "user"
        # Should include context about recent tokens
        user_text = messages[-1]["parts"][0]["text"]
        assert "Check PEPE" in user_text

    def test_synthesize_partial_response_no_calls(self) -> None:
        """Test synthesizing response with no tool calls."""
        mock_mcp = MagicMock()
        planner = AgenticPlanner(api_key="test", mcp_manager=mock_mcp)
        ctx = AgenticContext()

        result = planner._synthesize_partial_response("test query", ctx)

        assert "couldn't complete" in result.message.lower()

    def test_synthesize_partial_response_with_calls(self) -> None:
        """Test synthesizing response with tool calls."""
        mock_mcp = MagicMock()
        planner = AgenticPlanner(api_key="test", mcp_manager=mock_mcp)
        ctx = AgenticContext()
        ctx.tool_calls.append(
            ToolCall(
                client="dexpaprika",
                method="getNetworkPools",
                params={},
                result={"pools": []},
            )
        )

        result = planner._synthesize_partial_response("test query", ctx)

        assert "dexpaprika" in result.message
        assert "getNetworkPools" in result.message


class TestAgenticPlannerAsync:
    """Async tests for AgenticPlanner."""

    @pytest.mark.asyncio
    async def test_execute_single_tool_unknown_client(self) -> None:
        """Test executing tool with unknown client."""
        mock_mcp = MagicMock()
        mock_mcp.get_client = MagicMock(return_value=None)

        planner = AgenticPlanner(api_key="test", mcp_manager=mock_mcp)
        ctx = AgenticContext()

        # Create mock function call
        mock_fc = MagicMock()
        mock_fc.name = "unknown_someMethod"
        mock_fc.args = {}

        result = await planner._execute_single_tool(mock_fc, ctx)

        assert "error" in result
        assert "unknown" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_execute_single_tool_success(self) -> None:
        """Test executing tool successfully."""
        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value={"data": "test"})

        mock_mcp = MagicMock()
        mock_mcp.get_client = MagicMock(return_value=mock_client)

        planner = AgenticPlanner(api_key="test", mcp_manager=mock_mcp)
        ctx = AgenticContext()

        mock_fc = MagicMock()
        mock_fc.name = "test_someMethod"
        mock_fc.args = {"param": "value"}

        result = await planner._execute_single_tool(mock_fc, ctx)

        assert result == {"data": "test"}
        mock_client.call_tool.assert_awaited_once_with("someMethod", {"param": "value"})

    @pytest.mark.asyncio
    async def test_execute_single_tool_error(self) -> None:
        """Test executing tool that raises an error."""
        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(side_effect=RuntimeError("API error"))

        mock_mcp = MagicMock()
        mock_mcp.get_client = MagicMock(return_value=mock_client)

        planner = AgenticPlanner(api_key="test", mcp_manager=mock_mcp)
        ctx = AgenticContext()

        mock_fc = MagicMock()
        mock_fc.name = "test_failingMethod"
        mock_fc.args = {}

        result = await planner._execute_single_tool(mock_fc, ctx)

        assert "error" in result
        assert "API error" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_tools_parallel(self) -> None:
        """Test parallel execution of multiple tools."""
        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value={"success": True})

        mock_mcp = MagicMock()
        mock_mcp.get_client = MagicMock(return_value=mock_client)

        planner = AgenticPlanner(api_key="test", mcp_manager=mock_mcp)
        ctx = AgenticContext()

        mock_fcs = []
        for i in range(3):
            fc = MagicMock()
            fc.name = f"test_method{i}"
            fc.args = {}
            mock_fcs.append(fc)

        results = await planner._execute_tools_parallel(mock_fcs, ctx)

        assert len(results) == 3
        assert ctx.total_tool_calls == 3
        for result in results:
            assert result == {"success": True}


class TestSystemPrompt:
    """Tests for system prompt."""

    def test_system_prompt_contains_guidelines(self) -> None:
        """Test that system prompt contains key guidelines."""
        assert "honeypot" in AGENTIC_SYSTEM_PROMPT.lower()
        assert "dexpaprika" in AGENTIC_SYSTEM_PROMPT.lower()
        assert "dexscreener" in AGENTIC_SYSTEM_PROMPT.lower()
        assert "base" in AGENTIC_SYSTEM_PROMPT.lower()

    def test_system_prompt_has_workflow(self) -> None:
        """Test that system prompt has workflow section."""
        assert "Workflow" in AGENTIC_SYSTEM_PROMPT
        assert "Analyze" in AGENTIC_SYSTEM_PROMPT
