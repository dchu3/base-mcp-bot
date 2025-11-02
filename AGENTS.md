# Repository Guidelines

Follow these conventions when contributing to the Base MCP Telegram bot.

## Project Structure & Module Organization
Everything ships from `app/`. `main.py` wires the Telegram dispatcher, `config.py` loads environment settings, `handlers/` contains chat commands, `planner.py` orchestrates Gemini + MCP calls, `mcp_client.py` handles subprocess I/O, `store/` wraps the SQLite layer, `jobs/` runs APScheduler tasks, and `utils/` holds formatting, logging, prompt, and router helpers. Reusable planner copy lives in `prompts/planner.md`. Automation scripts sit in `scripts/`, and tests mirror the runtime modules under `tests/`. Use `.env.example` as the canonical reference when introducing new configuration keys.

## Build, Test, and Development Commands
- `./scripts/install.sh` provisions `.venv`, installs dependencies with `pip --no-cache-dir`, and pins tooling versions.
- `source .venv/bin/activate && ./scripts/start.sh` launches the bot and the configured Base and Dexscreener MCP servers; pass extra flags after the script name to tweak logging.
- `python -m app.main` runs the bot directly if MCP processes are already managed externally.
- `pytest`, `ruff check`, and `black --check .` are the required validation steps before opening a pull request.

## Coding Style & Naming Conventions
Target Python 3.11 with PEP 8 defaults (4-space indentation, snake_case functions, PascalCase classes, UPPER_SNAKE_CASE constants). Type hints are expected on public APIs, and docstrings should focus on side effects or protocol contracts. Always run `ruff` and `black` via the commands above.

## Testing Guidelines
Place unit tests beside their runtime counterparts under `tests/`, naming files `test_<module>.py`. Use `pytest` fixtures to stub Gemini and MCP interactions; add an `integration` marker when Dockerized or network-dependent behaviour is exercised so CI can skip it. Keep subscription and planner scenarios covered when altering schemas, and document any new canned payloads in the test module.

## Commit & Pull Request Guidelines
Write imperative, ≤72-character commit subjects with optional wrapped bodies (100 columns). Reference tickets using `Refs:` or `Fixes:` when applicable. Pull requests should summarize behavioural changes, list manual verification steps (bot commands run, MCP responses inspected), attach screenshots for Telegram UX updates, and call out new environment variables or script requirements.

## MCP & Configuration Tips
Never commit populated `.env` files or SQLite artefacts; `.gitignore` already excludes `.env`, `.tmp/`, and `state.db`. Ensure `PLANNER_PROMPT_FILE`, MCP command paths, and optional `TELEGRAM_CHAT_ID` are kept in `.env`. When sharing command examples, prefer relative paths or placeholders so secrets and machine-specific directories stay out of version control.
