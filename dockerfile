# syntax=docker/dockerfile:1.4
# ==============================================================================
# Multi-stage Dockerfile: Go (frontend) + Python FastAPI (ML backend)
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build Go binary (static, musl-compatible)
# ------------------------------------------------------------------------------
FROM golang:1.25-alpine AS go-builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -a -installsuffix cgo \
    -ldflags="-w -s -extldflags '-static'" \
    -o bin/main .

# ------------------------------------------------------------------------------
# Stage 2: Python ML service (dependencies + app)
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS python-builder
WORKDIR /app

# Системные зависимости для сборки
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Кэшируем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем приложение
COPY api.py onnx_pipeline_3_.py seed_data.py ./
COPY models/ ./models/
COPY test/ ./test/
COPY other/ ./other/

# ✅ FIX: Use patchelf instead of execstack (Debian trixie compatible)
RUN apt-get update && apt-get install -y --no-install-recommends patchelf && \
    find /usr/local/lib/python3.11/site-packages/onnxruntime -name "*.so" \
        -exec patchelf --clear-execstack {} \; 2>/dev/null || true && \
    apt-get remove -y patchelf && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------------------
# Stage 3: Final minimal runtime image
# ------------------------------------------------------------------------------
FROM python:3.11-slim
# Metadata
ARG BUILD_DATE
ARG VCS_REF
LABEL org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/yourorg/fraudreturn-shield" \
      org.opencontainers.image.revision="${VCS_REF}"

WORKDIR /app

# Минимальные системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl postgresql-client libpq5 dumb-init \
    && rm -rf /var/lib/apt/lists/* && apt-get clean

# Создаём не-привилегированного пользователя
RUN useradd -m -u 1000 -s /bin/bash appuser

# Копируем Go-бинарник и статику
COPY --from=go-builder --chown=appuser:appuser /app/bin/main ./bin/main

# Копируем Python-окружение ТОЛЬКО необходимое
COPY --from=python-builder --chown=appuser:appuser \
    /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-builder --chown=appuser:appuser \
    /usr/local/bin /usr/local/bin

# Копируем приложение
COPY --chown=appuser:appuser \
    api.py onnx_pipeline_3_.py seed_data.py entrypoint.sh ./
COPY --chown=appuser:appuser \
    models/ ./models/
COPY --chown=appuser:appuser \
    test/ ./test/
COPY --chown=appuser:appuser \
    other/ ./other/
COPY --chown=appuser:appuser \
    static/ ./static/
COPY --chown=appuser:appuser \
    templates/ ./templates/

# Права на исполнение
RUN chmod +x ./bin/main ./entrypoint.sh ./seed_data.py && \
    find ./test -name "*.py" -exec chmod +x {} \; 2>/dev/null || true

USER appuser

# Environment variables
ENV PORT=:8083 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/usr/local/bin:$PATH" \
    DB_HOST=db \
    DB_PORT=5432 \
    DB_USER=postgres \
    DB_PASSWORD=postgres \
    DB_NAME=fraudreturn \
    MODEL_PATH=./models/fraud_model_v4_27patterns.onnx \
    PYTHONPATH=/app

# Healthcheck: проверяем основной сервис (Go), ML-сервис проверяется косвенно
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD curl -f http://localhost:8083/api/stats || exit 1

# Entrypoint с dumb-init для корректной обработки сигналов
ENTRYPOINT ["/usr/bin/dumb-init", "--"]

# Запуск через entrypoint-скрипт
CMD ["/app/entrypoint.sh"]