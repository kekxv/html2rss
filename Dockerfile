# Use a multi-stage build to keep the final image small
FROM ghcr.io/astral-sh/uv:python3.12-slim AS builder

# Set working directory
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy project files
COPY pyproject.toml uv.lock ./

# Install dependencies into a virtual environment
RUN uv sync --frozen --no-dev --no-install-project

# Final stage
FROM python:3.12-slim

WORKDIR /app

# Copy the virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv

# Copy the application code
COPY main.py ./
COPY webroot ./webroot

# Ensure the virtual environment is used
ENV PATH="/app/.venv/bin:$PATH"

# Default port
EXPOSE 3000

# Run the application
ENTRYPOINT ["python", "main.py"]
CMD ["--port=3000", "--verification-code=test"]
