# Project conventions

Python 3.13, uv, FastAPI and Streamlit. Keep calculations independent of HTTP and UI.
Run `uv run pytest` and `uv run ruff check .` after changes to business logic.
Use Decimal for monetary arithmetic. Never label synthetic prices or latency as measured.
Do not call paid model APIs unless explicitly requested. Never commit credentials.
Keep `docs/architecture.md` and README consistent with implemented behavior.
