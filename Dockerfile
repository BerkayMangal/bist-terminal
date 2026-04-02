# ================================================================
# BISTBULL TERMINAL V10.0 — Docker Build (Railway)
# Single process: FastAPI + background scanner + WebSocket
# Redis connection via REDIS_URL env var (optional)
# ================================================================

FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create persistent data directory (mount volume at /data on Railway)
RUN mkdir -p /data

# Railway sets $PORT dynamically; default 8080 for local dev
EXPOSE 8080

# Single process entry — uvicorn serves HTTP + WebSocket
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
