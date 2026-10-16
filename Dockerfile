# Stage 1: build the Next.js static export (/mobile, /monitor) to frontend/out.
FROM oven/bun:1.4 AS frontend
WORKDIR /app/frontend
ENV NEXT_TELEMETRY_DISABLED=1
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun --bun run build

# Stage 2: the backend. FastAPI serves the static export from STATIC_DIR.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app/backend
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev
COPY --from=frontend /app/frontend/out /app/frontend/out
ENV STATIC_DIR=/app/frontend/out
CMD ["/app/backend/.venv/bin/uvicorn", "muthoot_collections.main:app", "--host", "0.0.0.0", "--port", "8080"]
