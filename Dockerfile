# Voice Gate for Alexa+ - Container Image
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    VOICEGATE_DATA=/app/voiceprints \
    VOICEGATE_MODEL_DIR=/app/pretrained_models \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8765

# System dependencies for audio reading (libsndfile)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure storage directories exist
RUN mkdir -p /app/voiceprints /app/pretrained_models

EXPOSE 8000 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/users || exit 1

CMD ["python", "run.py"]
