# The A2UI GE agent (collections_ui_agent) on Cloud Run. GE calls it over A2A JSON-RPC.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY collections_agent ./collections_agent
COPY collections_ui_agent ./collections_ui_agent
CMD ["/app/.venv/bin/uvicorn", "collections_ui_agent.server:app", "--host", "0.0.0.0", "--port", "8080"]
