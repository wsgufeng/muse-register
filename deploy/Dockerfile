# Muse Register — 服务器模式镜像
# 构建（仓库根目录）: docker compose -f deploy/docker-compose.yml build
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl 供容器健康检查用；其余系统库由 playwright --with-deps 补齐。
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-server.txt ./
RUN pip install -r requirements.txt -r requirements-server.txt \
    && python -m playwright install chromium --with-deps

COPY . .
RUN mkdir -p /app/data

# 全部状态（数据库、日志、.env）都在这个卷里，镜像重建不丢。
VOLUME ["/app/data"]

EXPOSE 8799

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8799/api/health || exit 1

CMD ["python", "web.py", "--server", "--port", "8799"]
