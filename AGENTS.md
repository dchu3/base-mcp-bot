# Repository Guidelines

These guidelines keep contributions consistent for the Base MCP Telegram bot. Review them before opening a pull request.

## Project Structure & Module Organization
Source lives under `app/`, with feature logic split into `handlers/`, orchestration helpers in `planner.py` and `mcp_client.py`, data access in `store/`, and shared helpers in `utils/`. Configuration helpers (`config.py`, `.env.example`) sit at the package root. Tests mirror the package layout inside `tests/`. Runtime assets such as `routers.base.json`, the SQLite `state.db`, and Docker artefacts (`Dockerfile`, `deploy/docker-compose.yml`) stay in the repository root. Keep new modules small and feature-focused; wire them through `app/main.py`.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate`: create and activate a local virtual environment.
- `pip install -r requirements.txt`: install runtime and development dependencies; update this file whenever dependencies change.
- `python -m app.main`: run the bot locally; ensure required environment variables are loaded via `.env`.
- `docker compose -f deploy/docker-compose.yml up --build`: launch the bot with bundled MCP servers for end-to-end checks.

## Coding Style & Naming Conventions
Follow PEP 8 with 4-space indentation, type hints on public functions, and descriptive snake_case names. Use `CamelCase` for classes and `UPPER_SNAKE_CASE` for constants. Run `ruff check` for linting, `black` for formatting, and keep docstrings concise, describing side effects and tool interactions.

## Testing Guidelines
Unit tests belong in `tests/` with filenames matching `test_<module>.py`. Use `pytest` fixtures to mock MCP servers and Telegram APIs. Aim for ≥85% coverage on planner, formatting, and database layers; add integration smoke tests that depend on docker-compose and guard them with `pytest -m "integration"`. Document any new fixtures or sample payloads alongside the tests.

## Commit & Pull Request Guidelines
Existing history uses short, imperative summaries (`initial setup`, `Initial commit`); keep subject lines under 72 characters and body paragraphs wrapped at 100. Reference issues when applicable, list key changes in bullet form, and note follow-up tasks. PRs must mention environment or schema changes, include screenshots for Telegram UX tweaks, link to relevant MCP tool specs, and describe manual verification steps.

## Security & Configuration Tips
Never commit real API tokens or `state.db`; rely on `.env` templates and gitignored storage. Validate new MCP commands against rate limits and ensure responses append the “not financial advice” footer whenever prices surface. Rotate credentials before demos and scrub logs of PII.
