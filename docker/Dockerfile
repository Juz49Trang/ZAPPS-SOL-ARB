FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY src/ ./src/
COPY config/ ./config/

# Create necessary directories
RUN mkdir -p logs data

# Create non-root user
RUN useradd -m -u 1000 bot && chown -R bot:bot /app
USER bot

# Expose metrics port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8000')" || exit 1

# Run bot
CMD ["python", "-u", "src/arbitrage_bot.py"]