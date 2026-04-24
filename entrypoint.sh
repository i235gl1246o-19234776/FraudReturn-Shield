#!/bin/bash
set -e

echo "🚀 Initializing FraudReturn Shield..."

# Функция ожидания PostgreSQL
wait_for_db() {
    echo "⏳ Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT}..."
    until pg_isready -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" 2>/dev/null; do
        echo "   ...retrying in 2s"
        sleep 2
    done
    echo "✅ PostgreSQL is ready."
}

# Ждём БД
wait_for_db

# Запускаем seed-скрипт (идемпотентный)
echo "📦 Running seed script..."
python3 /app/seed_data.py

# Запускаем Python ML-сервис в фоне
echo "🐍 Starting Python ML service on port 8000..."
python3 -m uvicorn api:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level info &

ML_PID=$!

# Даём время на старт
sleep 3

# Проверяем, что ML-сервис запустился
if ! kill -0 $ML_PID 2>/dev/null; then
    echo "❌ Python ML service failed to start"
    exit 1
fi

# Запускаем Go-сервис на переднем плане (exec заменяет процесс)
echo "🔷 Starting Go web server on port ${PORT:-8083}..."
exec ./bin/main