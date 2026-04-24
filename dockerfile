# Multi-stage Dockerfile for FraudReturn Shield (Go + Python FastAPI)
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build Go binary (static compilation)
# ------------------------------------------------------------------------------
FROM golang:1.25-alpine AS go-builder

WORKDIR /app

RUN apk add --no-cache git ca-certificates

COPY go.mod go.sum ./
RUN go mod download

COPY main.go ./
COPY models ./models/
COPY static ./static/
COPY templates ./templates/

# Статическая сборка Go (не зависит от GLIBC)
RUN CGO_ENABLED=0 GOOS=linux go build -a -installsuffix cgo \
    -ldflags="-w -s" -o main .

# ------------------------------------------------------------------------------
# Stage 2: Python FastAPI ML service
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS python-service

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# ✅ Копируем ВСЕ Python-файлы и папки (кроме old_files и venv)
COPY api.py onnx_pipeline_3_.py seed_data.py ./
COPY models/ ./models/
COPY test/ ./test/
COPY other/ ./other/
COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# FIX: execstack для onnxruntime
RUN apt-get update && apt-get install -y --no-install-recommends execstack \
    && find /usr/local/lib/python3.11/site-packages/onnxruntime -name "*.so" -exec execstack -c {} \; 2>/dev/null || true \
    && apt-get remove -y execstack && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
# ------------------------------------------------------------------------------
# Stage 3: Final runtime image
# ------------------------------------------------------------------------------
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl postgresql-client libpq5 \
    && rm -rf /var/lib/apt/lists/* && apt-get clean

# Go-бинарник и статику
COPY --from=go-builder /app/main ./main
COPY --from=go-builder /app/models ./models/
COPY --from=go-builder /app/static ./static/
COPY --from=go-builder /app/templates ./templates/

# Всю Python-инсталляцию
COPY --from=python-service /usr/local /usr/local

# ✅ ВСЕ файлы и папки приложения (кроме old_files и venv)
COPY api.py onnx_pipeline_3_.py seed_data.py ./
COPY models/ ./models/
COPY test/ ./test/
COPY other/ ./other/
COPY static/ ./static/
COPY templates/ ./templates/

# Права и пользователь
RUN useradd -m -u 1000 -s /bin/bash appuser && \
    chown -R appuser:appuser /app && \
    chmod +x ./main ./seed_data.py && \
    find /app/test -name "*.py" -exec chmod +x {} \; 2>/dev/null || true

USER appuser

EXPOSE 8083 8000

ENV PORT=:8083 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/usr/local/bin:$PATH" \
    DB_HOST=db \
    DB_PORT=5432 \
    DB_USER=postgres \
    DB_PASSWORD=postgres \
    DB_NAME=fraudreturn \
    MODEL_PATH=./models/fraud_model_v4_27patterns.onnx

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8083/api/stats || exit 1

# INLINE CMD
CMD ["sh", "-c", "\
    echo '🚀 Initializing FraudReturn Shield...' && \
    echo '⏳ Waiting for PostgreSQL...' && \
    until pg_isready -h ${DB_HOST} -p ${DB_PORT} -U ${DB_USER} -d ${DB_NAME} 2>/dev/null; do \
        echo '   ...retrying in 2s' && sleep 2; \
    done && \
    echo '✅ PostgreSQL is ready.' && \
    echo '📦 Running seed script...' && \
    python3 /app/seed_data.py && \
    echo '🐍 Starting Python ML service on port 8000...' && \
    python3 -m uvicorn api:app --host 0.0.0.0 --port 8000 --workers 1 & \
    sleep 3 && \
    echo '🔷 Starting Go web server on port ${PORT:-8083}...' && \
    exec ./main \
"]