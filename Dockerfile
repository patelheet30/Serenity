# Build stage - install dependencies
FROM python:3.13-slim AS builder

WORKDIR /app

# Install system dependencies for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install UV
RUN pip install --no-cache-dir uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies in a virtual environment
RUN uv sync --frozen --no-dev

# Runtime stage - minimal image
FROM python:3.13-slim

WORKDIR /app

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install UV for running the application
RUN pip install --no-cache-dir uv

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY serenity/ ./serenity/
COPY main.py ./

# Create data directory (will be mounted as volume)
RUN mkdir -p /data

# Set environment variables
ENV DATABASE_PATH=/data/auto_slowmode.db
ENV PATH="/app/.venv/bin:$PATH"


# Run the bot
CMD ["uv", "run", "main.py"]