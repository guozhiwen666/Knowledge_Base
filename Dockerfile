# =============================================================================
# 知识库管理平台 · 后端镜像
#
# 构建：docker build -t kb-platform:1.0 .
# 运行：依赖 docker-compose.yml（含 mysql / redis / minio），或自行注入 .env
#
# 说明：
#   * 基镜像 python:3.14-slim（与 pyproject requires-python>=3.14 对齐）
#   * 用 uv 按 uv.lock 锁版本安装依赖，保证可复现
#   * 生产用 gunicorn + uvicorn worker 多进程；前端由后端挂载在 /
# =============================================================================
FROM python:3.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 系统依赖：curl 供健康检查探活；其余保持最小
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

# 先装依赖（利用层缓存：依赖不变则不重装）
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project

# 再拷代码
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY scripts/ /app/scripts/

WORKDIR /app/backend

EXPOSE 8000

# gunicorn 多 worker；worker 数可用环境变量覆盖。
# 注意：多 worker 下 FAQ 缓存/会话/限流/令牌黑名单依赖 Redis（见 docker-compose）。
CMD ["sh", "-c", \
     "uv run --no-sync gunicorn -k uvicorn.workers.UvicornWorker \
        -w ${GUNICORN_WORKERS:-4} -b 0.0.0.0:8000 --timeout 120 main:app"]
