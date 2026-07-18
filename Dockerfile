# Deep Research Agent —— 生产镜像（多阶段：前端 Vite 构建 → Python 运行）

# ---- 前端构建：Vite 产出静态 SPA 到 /fe/dist ----
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 运行镜像：纯 Python，asyncpg/sqlalchemy 均有 manylinux wheel，无需编译器 ----
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖：requirements 不变时这层走缓存，改代码不触发重装
COPY requirements.txt ./
RUN pip install -r requirements.txt

# 再拷应用代码 + 迁移脚本 + 前端静态页
COPY deep_research ./deep_research
COPY alembic ./alembic
COPY alembic.ini ./
# 前端：只拷 Vite 构建产物（api.py 优先加载 frontend/dist/index.html）
COPY --from=frontend /fe/dist ./frontend/dist
COPY docker/entrypoint.sh ./docker/entrypoint.sh
RUN chmod +x docker/entrypoint.sh && mkdir -p /app/data

# 非 root 运行：应用层被攻破时不直接获得容器 root。
# chown /app：entrypoint 跑 alembic，且 SQLite 默认库（无 DATABASE_URL 时）写在 /app 下
RUN useradd --create-home --uid 10001 --user-group appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 容器探针：命中 /healthz（slim 无 curl，用 stdlib urllib）
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

# entrypoint：先 alembic upgrade head 建/升级表，再起 uvicorn
ENTRYPOINT ["./docker/entrypoint.sh"]
