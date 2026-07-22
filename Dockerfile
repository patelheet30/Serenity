FROM python:3.13-slim AS builder
WORKDIR /app

# The modern way to install uv: Copy the pre-compiled binary directly from Astral's official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Optimize uv behavior for Docker
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Copy dependency files first
COPY pyproject.toml uv.lock ./

# Install dependencies using a Docker cache mount to make rebuilds instant
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy your application code
COPY serenity/ ./serenity/
COPY main.py ./

# Sync again to install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.13-slim
WORKDIR /app

# Install runtime dependencies (no need to install uv here!)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the fully built virtual environment and app code from the builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/serenity ./serenity
COPY --from=builder /app/main.py ./

RUN mkdir -p /data

# By placing the venv bin directory at the front of the PATH,
# "python" automatically uses the virtual environment.
ENV PATH="/app/.venv/bin:$PATH"
ENV DATABASE_PATH=/data/auto_slowmode.db

# Run the bot directly using Python, completely eliminating the need for uv in production!
CMD ["python", "main.py"]
