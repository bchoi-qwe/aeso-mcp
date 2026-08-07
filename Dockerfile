# syntax=docker/dockerfile:1

FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim AS runtime

RUN useradd --create-home --uid 10001 aeso \
 && mkdir -p /app \
 && chown -R aeso:aeso /app

WORKDIR /app
COPY --from=builder --chown=aeso:aeso /app/.venv /app/.venv
COPY --from=builder --chown=aeso:aeso /app/src /app/src
COPY --chown=aeso:aeso pyproject.toml README.md LICENSE server.json ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    AESO_MCP_LOG_LEVEL=INFO

USER aeso

EXPOSE 8000

# Default: Streamable HTTP for containers. Override CMD for stdio use-cases.
ENTRYPOINT ["aeso-mcp"]
CMD ["--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
