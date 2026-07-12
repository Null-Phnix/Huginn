# Huginn production image
FROM python:3.12-slim

WORKDIR /app

# Production uses the host StarSearch daemon, so the image contains only API
# runtime utilities by default and does not ship a second Chromium binary.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* && apt-get autoclean

# Resolve exactly the committed Python graph. The lock is generated with
# `uv lock`; production builds refuse to mutate it.
RUN pip install --no-cache-dir uv==0.10.4
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"

# Compatibility-only image variant. Build with
# `--build-arg HUGINN_INSTALL_PLAYWRIGHT_BROWSER=1` together with the explicit
# HUGINN_ALLOW_PLAYWRIGHT_FALLBACK setting; production Compose leaves this off.
ARG HUGINN_INSTALL_PLAYWRIGHT_BROWSER=0
RUN if [ "$HUGINN_INSTALL_PLAYWRIGHT_BROWSER" = "1" ]; then \
      python -m playwright install chromium --with-deps; \
    else \
      echo "StarSearch-only image: Playwright Chromium not installed"; \
    fi

# Copy application code
COPY README.md LICENSE /app/
COPY huginn/ /app/huginn/
COPY prompts/ /app/prompts/

# Create data directory
RUN mkdir -p /data && chmod 755 /data

EXPOSE 7432

ENV HUGINN_DATA_DIR=/data
ENV HUGINN_PORT=7432
ENV HUGINN_USER_AGENT="Huginn/Bot (+https://huginn.dev/bot)"

# Install an immutable package into the image (creates the `huginn` CLI).
RUN uv sync --locked --no-dev --no-editable

CMD ["huginn", "serve", "--host", "127.0.0.1", "--port", "7432"]
