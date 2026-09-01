.PHONY: judge-export judge-calibrate chaos-demo-worker run-worker install lock dependency-check audit sbom lint fmt test test-pg package package-smoke compose-check chaos-demo intent-train intent-eval up down down-clean migrate revision run-api run-cli fe-install fe-dev fe-build fe-lint fe-test help

PYTHON ?= python
UV ?= uv
PYTHON_SOURCES := deep_research tests alembic scripts eval
ifeq ($(OS),Windows_NT)
WHEEL_PYTHON := .wheel-smoke/Scripts/python.exe
WHEEL_CLI := .wheel-smoke/Scripts/deep-research.exe
else
WHEEL_PYTHON := .wheel-smoke/bin/python
WHEEL_CLI := .wheel-smoke/bin/deep-research
endif
WHEEL_FILE = $(firstword $(wildcard dist/deep_research_agent-*.whl))

help:  ## 显示可用命令
	@$(PYTHON) -c "import re; from pathlib import Path; [print(f'  {m.group(1):<12} {m.group(2)}') for line in Path('Makefile').read_text(encoding='utf-8').splitlines() if (m := re.match(r'^([A-Za-z_-]+):.*?## (.*)$$', line))]"

install:  ## 安装开发依赖（含 lint/类型/测试工具）
	$(PYTHON) -m pip install --require-hashes -r requirements-dev.lock

lock:  ## 根据人工维护的 requirements*.txt 更新带哈希锁文件（需要 uv）
	$(UV) pip compile requirements.txt --universal --python-version 3.11 --generate-hashes --custom-compile-command "make lock" --output-file requirements.lock
	$(UV) pip compile requirements-dev.txt --universal --python-version 3.11 --generate-hashes --custom-compile-command "make lock" --output-file requirements-dev.lock

dependency-check:  ## 校验直接依赖均被锁文件以兼容版本覆盖
	$(PYTHON) scripts/check_dependency_locks.py

audit: dependency-check  ## 审计 Python 与前端依赖漏洞
	$(PYTHON) -m pip_audit -r requirements.lock
	cd frontend && npm audit --audit-level=high

sbom:  ## 导出 Python 与前端依赖 SBOM 到 sbom/
	$(PYTHON) -c "from pathlib import Path; Path('sbom').mkdir(exist_ok=True)"
	$(PYTHON) -m pip_audit -r requirements.lock --progress-spinner off --format cyclonedx-json --output sbom/python-runtime.cdx.json
	cd frontend && npm sbom --package-lock-only --sbom-format cyclonedx > ../sbom/frontend.cdx.json

lint:  ## ruff 检查 + ruff 格式校验 + mypy 类型检查
	$(PYTHON) -m ruff check $(PYTHON_SOURCES)
	$(PYTHON) -m ruff format --check $(PYTHON_SOURCES)
	$(PYTHON) -m mypy deep_research

fmt:  ## 自动修复 + 格式化
	$(PYTHON) -m ruff check --fix $(PYTHON_SOURCES)
	$(PYTHON) -m ruff format $(PYTHON_SOURCES)

test:  ## 运行离线单元测试（跳过需要 PostgreSQL 的）
	$(PYTHON) -m pytest -m "not pg" --cov=deep_research --cov-report=term-missing --cov-fail-under=80

test-pg:  ## 运行需要 PostgreSQL 的集成测试
	$(PYTHON) -m deep_research.migrate
	$(PYTHON) -m pytest -m pg

package: fe-build  ## Build an installable wheel with the production SPA
	$(PYTHON) -c "from pathlib import Path; [path.unlink() for path in Path('dist').glob('deep_research_agent-*.whl')]"
	$(PYTHON) -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist

package-smoke: package  ## Verify the wheel in an isolated virtual environment
	$(PYTHON) -c "import shutil; from pathlib import Path; path = Path('.wheel-smoke'); shutil.rmtree(path) if path.exists() else None"
	$(PYTHON) -m venv .wheel-smoke
	$(WHEEL_PYTHON) -m pip install --require-hashes -r requirements.lock
	$(WHEEL_PYTHON) -m pip install --no-deps "$(WHEEL_FILE)"
	$(WHEEL_PYTHON) -m pip check
	$(WHEEL_CLI) --help
	$(WHEEL_PYTHON) scripts/smoke_installed_package.py

compose-check:  ## Render and validate Docker Compose configuration
	docker compose config --quiet

chaos-demo:  ## kill -9 故障恢复演示：杀 API 后重启接管（离线假后端，约 3 分钟）
	$(PYTHON) scripts/chaos_demo.py --target api

chaos-demo-worker:  ## kill -9 故障恢复演示：杀 worker，API 全程存活由另一 worker 接管
	$(PYTHON) scripts/chaos_demo.py --target worker

intent-train:  ## 训练意图识别 L2 本地模型（TF-IDF + 逻辑回归，纯离线）
	$(PYTHON) scripts/train_intent_model.py

intent-eval:  ## 意图识别离线评测（准确率 / 混淆矩阵 / 拒识率 / 误伤率 / 级联分流）
	$(PYTHON) -m eval.intent_eval

judge-export:  ## 从已落库 run 分层抽样，导出语义判定待标注文件
	$(PYTHON) -m eval.judge_calibration export

judge-calibrate:  ## 用已标注文件计算 judge 与人工的一致率 / Cohen's κ
	$(PYTHON) -m eval.judge_calibration report eval/calibration/semantic_cases.jsonl

up:  ## docker compose 起全栈（构建并启动）
	docker compose up --build

down:  ## 停止并清理容器（保留 pgdata/appdata 数据卷）
	docker compose down

down-clean:  ## 危险：停止服务并永久删除数据库与运行时配置数据卷
	docker compose down --volumes --remove-orphans

migrate:  ## 应用数据库迁移到最新版本
	$(PYTHON) -m deep_research.migrate

revision:  ## 生成迁移脚本：make revision m="变更说明"
	$(PYTHON) -m alembic revision --autogenerate -m "$(m)"

run-api:  ## 本地启动 API（从 .env 加载配置，热重载）
	$(PYTHON) -m uvicorn --env-file .env --reload deep_research.api:app

run-worker:  ## 本地启动执行 worker（需 DR_EXECUTION_MODE=worker 才有任务可领）
	$(PYTHON) -m dotenv run -- $(PYTHON) -m deep_research.worker

run-cli:  ## 本地跑 CLI（从 .env 加载配置）：make run-cli q="你的研究问题"
	$(PYTHON) -m dotenv run -- $(PYTHON) -m deep_research.cli "$(q)"

fe-install:  ## 按 package-lock.json 可复现安装前端依赖
	cd frontend && npm ci

fe-dev:  ## 启动前端开发服务器（Vite，proxy 到 :8000）
	cd frontend && npm run dev

fe-build:  ## 构建前端到 frontend/dist
	cd frontend && npm run build

fe-lint:  ## 前端 ESLint 检查
	cd frontend && npm run lint

fe-test:  ## 前端单元测试（vitest）
	cd frontend && npm run test
