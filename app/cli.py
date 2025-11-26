"""CLI interface for Base MCP Bot.

Run queries from the command line without Telegram restrictions.

Usage:
    python -m app.cli "show me uniswap activity"
    python -m app.cli --interactive
    python -m app.cli --output json "trending tokens"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any, Dict, List, Optional

from app.config import load_settings
from app.mcp_client import MCPManager
from app.simple_planner import SimplePlanner
from app.cli_output import CLIOutput, OutputFormat
from app.utils.logging import configure_logging, get_logger
from app.utils.routers import load_router_map

logger = get_logger(__name__)


async def run_single_query(
    planner: SimplePlanner,
    query: str,
    output: CLIOutput,
    context: Dict[str, Any],
) -> None:
    """Execute a single query and display the result."""
    output.status(f"Processing: {query}")

    try:
        result = await planner.run(query, context)
        output.result(result)
    except Exception as exc:
        output.error(f"Query failed: {exc}")
        raise


async def run_interactive(
    planner: SimplePlanner,
    output: CLIOutput,
) -> None:
    """Run interactive REPL session."""
    output.info("Base MCP Bot CLI - Interactive Mode")
    output.info("Type your queries, or use /quit to exit, /clear to reset context")
    output.info("-" * 50)

    context: Dict[str, Any] = {}
    conversation_history: List[Dict[str, str]] = []
    recent_tokens: List[Dict[str, str]] = []

    while True:
        try:
            query = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            output.info("\nGoodbye!")
            break

        if not query:
            continue

        # Handle commands
        if query.startswith("/"):
            cmd = query.lower()
            if cmd in ("/quit", "/exit", "/q"):
                output.info("Goodbye!")
                break
            elif cmd in ("/clear", "/reset"):
                conversation_history.clear()
                recent_tokens.clear()
                output.info("Context cleared.")
                continue
            elif cmd in ("/help", "/h"):
                output.info("Commands: /quit, /clear, /help, /routers")
                continue
            elif cmd == "/routers":
                from app.utils.routers import list_routers

                output.info("Available routers:")
                for key, name, _ in list_routers("base-mainnet"):
                    output.info(f"  - {name} ({key})")
                continue
            else:
                output.warning(f"Unknown command: {query}")
                continue

        # Build context with conversation history
        context = {
            "conversation_history": conversation_history,
            "recent_tokens": recent_tokens,
        }

        try:
            result = await planner.run(query, context)
            output.result(result)

            # Update conversation history
            conversation_history.append({"role": "user", "content": query})
            conversation_history.append(
                {"role": "assistant", "content": result.message}
            )

            # Keep history bounded
            if len(conversation_history) > 20:
                conversation_history = conversation_history[-20:]

            # Update token context
            if result.tokens:
                recent_tokens = result.tokens[:10]

        except Exception as exc:
            output.error(f"Error: {exc}")


async def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Base MCP Bot CLI - Query Base blockchain without Telegram",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m app.cli "show me uniswap activity"
  python -m app.cli --interactive
  python -m app.cli --output json "trending tokens"
  python -m app.cli --verbose "check 0x..."
        """,
    )

    parser.add_argument(
        "query",
        nargs="?",
        help="Natural language query (e.g., 'show me PEPE')",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Start interactive REPL mode",
    )
    parser.add_argument(
        "-o",
        "--output",
        choices=["text", "json", "rich"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show debug information",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read query from stdin",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Disable AI insights/synthesis",
    )

    args = parser.parse_args()

    # Determine output format
    try:
        output_format = OutputFormat(args.output)
    except ValueError:
        output_format = OutputFormat.TEXT

    output = CLIOutput(format=output_format, verbose=args.verbose)

    # Validate arguments
    if not args.interactive and not args.query and not args.stdin:
        parser.print_help()
        sys.exit(1)

    # Get query from stdin if requested
    query: Optional[str] = args.query
    if args.stdin:
        query = sys.stdin.read().strip()
        if not query:
            output.error("No query provided via stdin")
            sys.exit(1)

    # Load settings
    try:
        settings = load_settings()
    except Exception as exc:
        output.error(f"Failed to load settings: {exc}")
        output.info("Ensure .env file exists with GEMINI_API_KEY set")
        sys.exit(1)

    # Configure logging
    log_level = "DEBUG" if args.verbose else settings.log_level
    configure_logging(log_level)

    # Initialize MCP manager
    output.status("Starting MCP servers...")
    mcp_manager = MCPManager(
        base_cmd=settings.mcp_base_server_cmd,
        dexscreener_cmd=settings.mcp_dexscreener_cmd,
        honeypot_cmd=settings.mcp_honeypot_cmd,
    )

    try:
        await mcp_manager.start()
        output.status("MCP servers ready")
    except Exception as exc:
        output.error(f"Failed to start MCP servers: {exc}")
        sys.exit(1)

    # Initialize planner
    router_map = load_router_map()
    planner = SimplePlanner(
        api_key=settings.gemini_api_key,
        mcp_manager=mcp_manager,
        model_name=settings.gemini_model,
        router_map=router_map,
        enable_ai_insights=not args.no_ai,
    )

    try:
        if args.interactive:
            await run_interactive(planner, output)
        elif query:
            await run_single_query(planner, query, output, context={})
    except KeyboardInterrupt:
        output.info("\nInterrupted")
    finally:
        output.status("Shutting down MCP servers...")
        await mcp_manager.shutdown()


def cli_main() -> None:
    """Synchronous wrapper for CLI entry."""
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()
