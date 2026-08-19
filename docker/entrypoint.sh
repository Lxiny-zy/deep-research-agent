#!/usr/bin/env sh
# 容器启动：先把数据库迁移到最新版本，再拉起 API。
# 迁移失败则直接退出（set -e），避免在错误 schema 上跑服务。
set -e

echo "[entrypoint] validating production configuration ..."
python -c "from deep_research.config import Settings; Settings().validate_deployment()"

echo "[entrypoint] alembic upgrade head ..."
python -m deep_research.migrate

if [ "$#" -eq 0 ]; then
  set -- python -m uvicorn deep_research.api:app --host 0.0.0.0 --port 8000
fi

echo "[entrypoint] starting: $*"
exec "$@"
