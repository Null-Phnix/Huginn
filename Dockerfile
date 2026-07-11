# Huginn production image
FROM python:3.12-slim

WORKDIR /app

# Install Playwright system deps + runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates procps \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 \
    libcairo2 libatspi2.0-0 fonts-liberation \
    && rm -rf /var/lib/apt/lists/* && apt-get autoclean

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and Chromium browser
RUN python -m playwright install chromium --with-deps

# Copy application code
COPY huginn/ /app/huginn/
COPY prompts/ /app/prompts/
COPY pyproject.toml .
COPY README.md LICENSE ./

# Create data directory
RUN mkdir -p /data && chmod 755 /data

EXPOSE 7432

ENV HUGINN_DATA_DIR=/data
ENV HUGINN_PORT=7432
ENV HUGINN_USER_AGENT="Huginn/Bot (+https://huginn.dev/bot)"

# Install an immutable package into the image (creates the `huginn` CLI).
RUN pip install --no-cache-dir --no-deps .

CMD ["huginn", "serve", "--host", "0.0.0.0", "--port", "7432"]
