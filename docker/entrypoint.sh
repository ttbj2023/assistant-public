#!/bin/bash
set -e

# config.yaml 已在 Dockerfile 内 COPY (镜像内置兜底);
# 生产环境通过 docker-compose volume 挂载覆盖 (../config.yaml:/app/config.yaml:ro).
if [ ! -f /app/config.yaml ]; then
    echo "[ERROR] /app/config.yaml missing; image build broken or volume mount misconfigured"
    exit 1
fi

# 确保数据目录存在
mkdir -p /app/data

echo "[INFO] Starting assistant (ENVIRONMENT=${ENVIRONMENT:-production})"
echo "[INFO] API_PORT=${API_PORT:-8000}"

exec python -m uvicorn src.api.fastapi_app:app \
    --host "${API_HOST:-0.0.0.0}" \
    --port "${API_PORT:-8000}" \
    --workers 1 \
    --log-level info
