# Image of the remote connector (docs/remote/06-dev-cycle.md): ONE image, two
# Cloud Run services. The start command picks the service:
#
#   docker run IMAGE                          -> google_chat_mcp.settings (default, gcm-settings)
#   docker run IMAGE google_chat_mcp.remote   -> gcm-mcp
#
# On Cloud Run set the service's `args` (the entrypoint stays `python -m`).
# No secrets in the image: configuration comes from environment variables and
# Secret Manager at run time. The dependencies come from uv.lock.

ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: this layer is cached until uv.lock or pyproject.toml change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra remote --no-install-project
# Then the project itself, installed (not editable) into the same venv.
COPY google_chat_mcp ./google_chat_mcp
RUN uv sync --frozen --no-dev --extra remote --no-editable

FROM python:${PYTHON_VERSION}-slim
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER app
# Cloud Run sets PORT; the services read it.
EXPOSE 8080
ENTRYPOINT ["python", "-m"]
CMD ["google_chat_mcp.settings"]
