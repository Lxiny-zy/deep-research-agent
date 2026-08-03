.PHONY: install lint fmt test test-pg chaos-demo intent-train intent-eval up down migrate revision run-api run-cli fe-install fe-dev fe-build fe-lint fe-test help

PYTHON ?= python

help:  ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## 安装开发依赖（含 lint/类型/测试工具）
	$(PYTHON) -m pip install -r requirements-dev.txt

lint:  ## ruff 检查 + ruff 格式校验 + mypy 类型检查
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy deep_research

fmt:  ## 自动修复 + 格式化
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

test:  ## 运行离线单元测试（跳过需要 PostgreSQL 的）
	$(PYTHON) -m pytest -m "not pg"

test-pg:  ## 运行需要 PostgreSQL 的集成测试
	$(PYTHON) -m pytest -m pg

chaos-demo:  ## kill -9 故障恢复演示（离线假后端，全程约 3 分钟）
	$(PYTHON) scripts/chaos_demo.py

intent-train:  ## 训练意图识别 L2 本地模型（TF-IDF + 逻辑回归，纯离线）
	$(PYTHON) scripts/train_intent_model.py

intent-eval:  ## 意图识别离线评测（准确率 / 混淆矩阵 / 拒识率 / 误伤率 / 级联分流）
	$(PYTHON) -m eval.intent_eval

up:  ## docker compose 起全栈（构建并启动）
	docker compose up --build

down:  ## 停止并清理容器
	docker compose down -v

migrate:  ## 应用数据库迁移到最新版本
	$(PYTHON) -m alembic upgrade head

revision:  ## 生成迁移脚本：make revision m="变更说明"
	$(PYTHON) -m alembic revision --autogenerate -m "$(m)"

run-api:  ## 本地启动 API（热重载）
	$(PYTHON) -m uvicorn deep_research.api:app --reload

run-cli:  ## 本地跑 CLI：make run-cli q="你的研究问题"
	$(PYTHON) -m deep_research.cli "$(q)"

fe-install:  ## 安装前端依赖
	cd frontend && npm install

fe-dev:  ## 启动前端开发服务器（Vite，proxy 到 :8000）
	cd frontend && npm run dev

fe-build:  ## 构建前端到 frontend/dist
	cd frontend && npm run build

fe-lint:  ## 前端 ESLint 检查
	cd frontend && npm run lint

fe-test:  ## 前端单元测试（vitest）
	cd frontend && npm run test
