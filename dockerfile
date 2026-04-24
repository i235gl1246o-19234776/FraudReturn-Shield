# Multi-stage Dockerfile for FraudReturn Shield (Go + Python FastAPI)
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build Go binary (static compilation)
# ------------------------------------------------------------------------------
FROM golang:1.25-alpine AS go-builder

WORKDIR /app

# Install build dependencies
RUN apk add --no-cache git ca-certificates

# Copy Go module files first (for better layer caching)
COPY go.mod go.sum ./
RUN go mod download

# Copy source code
COPY main.go ./
COPY models ./models/
COPY static ./static/
COPY templates ./templates/

# Build static Go binary
RUN CGO_ENABLED=0 GOOS=linux go build -a -installsuffix cgo \
    -ldflags="-w -s" -o main .

# ------------------------------------------------------------------------------
# Stage 2: Python FastAPI ML service
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS python-service

WORKDIR /app

# Install system dependencies for ML packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy Python source files
COPY api.py onnx_pipeline_3_.py ./
COPY models ./models/

# Install Python dependencies (pinned versions for reproducibility)
COPY <<EOF /app/requirements.txt
fastapi==0.109.0
uvicorn[standard]==0.27.0
pandas==2.1.4
numpy==1.26.3
onnxruntime==1.16.3
tokenizers==0.15.0
rank-bm25==0.2.2
psycopg2-binary==2.9.9
pydantic==2.5.3
EOF

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# Pre-load model to verify it exists (optional, fails build if model missing)
#RUN python3 -c "import onnxruntime; print('ONNX runtime OK')" || exit 1

# ------------------------------------------------------------------------------
# Stage 3: Final runtime image (minimal + secure)
# ------------------------------------------------------------------------------
FROM debian:bookworm-slim

# Metadata
LABEL org.opencontainers.image.title="FraudReturn Shield" \
      org.opencontainers.image.description="Hybrid Go+Python fraud detection API" \
      org.opencontainers.image.version="1.0.0"

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    python3-pip \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy Go binary and static assets from builder
COPY --from=go-builder /app/main ./main
COPY --from=go-builder /app/models ./models/
COPY --from=go-builder /app/static ./static/
COPY --from=go-builder /app/templates ./templates/

# Copy Python environment and source from python-service
COPY --from=python-service /usr/local/lib/python3.11/site-packages \
    /usr/local/lib/python3.11/site-packages
COPY --from=python-service /usr/local/bin /usr/local/bin
COPY --from=python-service /app/api.py /app/onnx_pipeline_3_.py ./

# Create non-root user for security best practices
RUN useradd -m -u 1000 -s /bin/bash appuser && \
    chown -R appuser:appuser /app && \
    chmod +x ./main

USER appuser

# Expose application ports
EXPOSE 8083 8000

# Environment variables with sensible defaults
ENV PORT=:8083 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_HOST=localhost \
    DB_PORT=5432 \
    DB_USER=postgres \
    DB_PASSWORD=postgres \
    DB_NAME=fraudreturn \
    MODEL_PATH=./models/fraud_model_v4_27patterns.onnx

# Health check for Go web server
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8083/api/stats || exit 1

# Entrypoint script to start both Go and Python services
COPY <<'EOF' /app/entrypoint.sh
#!/bin/bash
set -e

# Start Python FastAPI ML service in background
echo "🚀 Starting Python ML service on port 8000..."
python3 -m uvicorn api:app --host 0.0.0.0 --port 8000 --workers 1 &
PYTHON_PID=$!

# Give Python service time to initialize
sleep 3

# Start Go web server (foreground)
echo "🚀 Starting Go web server on port ${PORT:-8083}..."
exec ./main
EOF

# Run via entrypoint to manage both services
# В конце Dockerfile:
CMD ["/bin/sh", "-c", "\
  python3 -m uvicorn api:app --host 0.0.0.0 --port 8000 & \
  sleep 3 && \
  exec ./main \
"]