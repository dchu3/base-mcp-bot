# Gemini Project Analysis: base-mcp-bot

This document provides an analysis of the `base-mcp-bot` project, a Telegram bot that uses Gemini for function planning to orchestrate Base and Dexscreener MCP servers.

## Project Overview

The `base-mcp-bot` is a sophisticated Telegram bot designed to provide users with real-time information about cryptocurrency tokens and transactions on the Base blockchain. It leverages the power of Google's Gemini Pro model to understand natural language queries, plan a series of actions, and execute them by calling various MCP (Massively Concurrent Processor) servers.

The bot can:

*   Fetch the latest transactions from decentralized exchange (DEX) routers.
*   Provide detailed summaries of tokens using data from Dexscreener.
*   Check tokens for honeypot risks.
*   Allow users to subscribe to real-time updates for specific DEX routers.
*   Respond to a wide range of natural language queries about transactions, tokens, and routers.

## Architecture

The bot is built using Python and follows a modular architecture. The key components are:

*   **Application Entrypoint (`app/main.py`):** Initializes and orchestrates all the components of the bot, including the Telegram bot, database, MCP manager, Gemini planner, and subscription service.
*   **Gemini Planner (`app/planner.py`):** This is the core of the bot's intelligence. It takes a user's message, constructs a detailed prompt for the Gemini model, and parses the model's JSON output to create a plan of tool invocations. It then executes this plan, calling the appropriate MCP servers, and formats the results into a user-friendly response.
*   **MCP Manager (`app/mcp_client.py`):** Manages the lifecycle of the MCP servers, which are run as subprocesses. It provides a clean interface for the planner to call tools on the `base`, `dexscreener`, and `honeypot` MCP servers.
*   **Telegram Handlers (`app/handlers/`):** Implements the command and message handlers for the Telegram bot. This includes both explicit commands (e.g., `/latest`, `/subscribe`) and the natural language query handler that uses the Gemini planner.
*   **Subscription Service (`app/jobs/subscriptions.py`):** Manages user subscriptions to router updates. It uses an `AsyncIOScheduler` to periodically poll for new transactions and notify users.
*   **Database (`app/store/`):** Uses SQLite to persist user data, subscriptions, and other settings.
*   **Configuration (`app/config.py`):** Loads settings from a `.env` file, allowing for easy configuration of API keys, server commands, and other parameters.

## Key Features

*   **Natural Language Understanding:** The bot uses Gemini to understand complex user queries and translate them into a series of tool calls. This allows for a much more intuitive user experience than traditional command-based bots.
*   **MCP Integration:** The bot is designed to work with MCP servers, which provide a standardized way to interact with various blockchain data sources.
*   **Subscription Model:** Users can subscribe to real-time updates for their favorite DEX routers, allowing them to stay informed about the latest market movements.
*   **Honeypot Detection:** The bot integrates with a honeypot detection service to warn users about potentially malicious tokens.
*   **Extensible Prompting:** The prompt used to guide the Gemini model can be easily customized, allowing developers to fine-tune the bot's behavior.
*   **Comprehensive Testing:** The project includes a suite of unit and integration tests to ensure the bot's reliability.

## How it Works

1.  A user sends a message to the Telegram bot.
2.  The message is received by the `MessageHandler` in `app/handlers/commands.py`.
3.  The handler calls the `GeminiPlanner.run()` method.
4.  The planner constructs a prompt for the Gemini model, including the user's message and other context.
5.  The Gemini model returns a JSON object containing a list of tool invocations.
6.  The planner executes the tool invocations by calling the appropriate methods on the MCP manager.
7.  The results of the tool calls are collected and formatted into a Markdown message.
8.  The message is sent back to the user via the Telegram bot.

## Conclusion

The `base-mcp-bot` is a well-engineered and powerful tool for anyone interested in the Base blockchain. Its use of Gemini for natural language understanding and planning makes it incredibly flexible and easy to use, while its modular architecture and comprehensive test suite make it a solid foundation for future development.
