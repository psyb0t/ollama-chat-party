# 🐳 Dockerfile for Ollama Chat Party
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the main application and supporting modules
COPY main.py .
COPY cli_handler_simple.py .
COPY shared_state.py .
COPY web_server.py .
COPY templates/ ./templates/
COPY static/ ./static/

# Create a directory for documents (can be mounted)
RUN mkdir -p /app/documents

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Expose port for web interface
EXPOSE 8000

# Health check to ensure the app can start
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import main; print('App loads successfully')" || exit 1

# Use python main.py as entrypoint
ENTRYPOINT ["python", "main.py"]
