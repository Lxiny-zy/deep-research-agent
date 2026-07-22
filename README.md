# Deep Research Agent · 多 Agent 深度研究系统

把「一个问题」自动**拆解 → 并行检索 → 反思补洞 → 综合成带引用的研究报告**的多 Agent 系统。

面向 AI Agent 工程岗位的简历项目，重点展示 **多 Agent 编排、并行 fan-out、来源安全策略、证据验证、流式可观测、自动化评估** 等工程能力（而非又一个对话机器人）。

---

## ✨ 亮点（面试可讲的点）

- **多 Agent 协作**：Planner / Researcher / Reflector / Synthesizer 各司其职，职责清晰。
- **并行 fan-out**：子问题用 `asyncio` 并发检索，墙钟时间 ≈ 最慢的一条链，而非求和。
- **反思循环**：Reflector 自评证据是否充分，不足则自动补洞（loop-until-sufficient）。
- **来源策略门禁**：检索内容进入 LLM 前检查 URL scheme、非公网/歧义 IP、嵌入凭据，以及网页标题/正文/URL path/query/fragment 中的中英文 Prompt Injection 信号；隔离/拒绝决策进入结构化事件审计。
- **论断证据门禁**：Finding 必须携带来源原文 `evidence_quote`；程序执行归一化逐字匹配并记录内容哈希，只有 `verified` 且语义判定为 `supported` 的论断能进入 Reflector / Synthesizer 和最终引用。
- **论断一致性标记**：程序为已支持论断生成稳定 `claim_id`，再做跨论断矛盾检测；冲突不会被静默丢弃，而是以 `conflicted`、反向 claim 链接和原因进入审计与报告素材。
- **流式可观测**：SSE 把每个 Agent 的动作实时推到浏览器；内置 Tracer 统计耗时 / token。
- **持久化与回放**：每次研究全过程落库（计划 / 结果 / 报告 / 事件）；提供历史列表、详情、SSE 事件回放。仓储接口双实现（内存 / async SQLAlchemy），本地 SQLite 零配置并在启动时准备 schema，生产切 PostgreSQL，Alembic 管 schema 版本。
- **provider 无关**：任意 OpenAI 兼容端点（OpenAI / DeepSeek / Qwen / GLM / Moonshot …）。
- **可测试**：依赖注入（LLM / 检索后端可替换为假实现），单测无需密钥与网络。
- **自动化评估**：内置 LLM-as-judge，从覆盖度/可靠性/深度/可读性四维给报告打分。

## 架构

```
用户问题
    │
    ▼
┌─────────┐  把大问题拆成若干可独立检索的子问题
│ Planner │
└─────────┘
    │ sub_questions[]
    ▼
┌──────────────────────────────┐  每个子问题一个 Researcher，
│ Researcher × N  (并行 fan-out) │  各自检索网络 → 抽取带出处的发现
└──────────────────────────────┘
    │ findings[]
    ▼
┌────────────┐  证据是否充分？缺什么？需要补哪些新子问题？
│ Reflector  │  （不充分则回到 Researcher，最多 N 轮）
└────────────┘
    │ (sufficient)
    ▼
┌─────────────┐  综合成结构化、带 [n] 引用的研究报告
│ Synthesizer │
└─────────────┘
    │
    ▼
Markdown 报告（含参考来源）
```

## 目录结构

```
deep-research-agent/
├── deep_research/
│   ├── config.py            # 配置（环境变量 + 行为参数）
│   ├── models.py            # Pydantic 数据模型（各 Agent 的 schema）
│   ├── observability.py     # Event + Tracer（控制台订阅 / SSE 队列）
│   ├── llm.py               # LLM 封装（complete + 流式 + 结构化 parse，provider 无关）
│   ├── guardrails.py        # 来源安全策略 + 原文证据确定性验证
│   ├── dag.py               # 子问题依赖图：构建 / 环检测 / 拓扑分层
│   ├── tools/               # 检索后端抽象 + Tavily 实现
│   ├── agents/              # Planner / Researcher / Reflector / Synthesizer
│   ├── persistence/         # 仓储层：接口 + InMemory / SQL(async SQLAlchemy) 双实现
│   ├── orchestrator.py      # 编排：DAG 调度 + 反思循环 + run_stream + 落库
│   ├── cli.py               # 命令行入口
│   └── api.py               # FastAPI + SSE（含历史 / 回放端点）
├── alembic/                 # 数据库迁移（env.py + versions/）
├── alembic.ini
├── Dockerfile               # 生产镜像（启动时迁移 + uvicorn）
├── docker-compose.yml       # 一键起 PostgreSQL + API
├── frontend/                # React + TS + Vite 工程化 SPA
│   ├── src/                 # 页面 / 组件 / hooks / API client / 类型
│   ├── vite.config.ts       # dev proxy → :8000；构建产物输出 dist/
│   └── package.json
├── eval/                    # LLM-as-judge 自动化评估
└── tests/                   # pytest（fakes 注入，无需网络）
```

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入 LLM_API_KEY 与 TAVILY_API_KEY

# 命令行
python -m deep_research.cli "2026 年主流 AI Agent 框架有哪些？各自取舍是什么？"
```

## Web 实时 Demo（React 前端）

前端是独立的 **React + TS + Vite** 工程（`frontend/`），与 FastAPI 后端分离开发、同源部署。

**开发模式**（前端热重载，推荐）：

```bash
# 终端 1：后端（:8000）
uvicorn deep_research.api:app --reload

# 终端 2：前端（Vite dev server，自动 proxy /api 与 /healthz 到 :8000）
cd frontend && npm install && npm run dev   # 打开 http://127.0.0.1:5173
```

**生产模式**（构建后由后端同源托管）：

```bash
cd frontend && npm install && npm run build  # 产出 frontend/dist
uvicorn deep_research.api:app                # 访问 http://127.0.0.1:8000（加载构建版 SPA）
```

页面提供：新建研究（可调研究参数）、实时观看（Agent 时间线 / DAG 分层调度 / 流式报告 / **实时统计**：耗时秒级跳动、token 随阶段累加）、**报告导出**（复制 / 下载 `.md`）、历史列表与回放、**历史管理**（删除单条·批量 / 状态·关键词·标签筛选 / 打标签分类）、**全局设置**（前端改模型 / 端点 / 密钥 / 检索参数并持久化）。后端 `GET /` 优先加载 `frontend/dist/index.html`，未构建时回退到占位页。

## Docker 一键启动（含 PostgreSQL）

```bash
cp .env.example .env   # 填入 LLM_API_KEY、TAVILY_API_KEY，并设置 POSTGRES_PASSWORD（必填）
docker compose up --build
# 启动后访问 http://127.0.0.1:8000
# 容器内置 Postgres，API 启动时自动 alembic upgrade head 建/升级表
docker compose down -v # 停止并清库（含数据卷）
```

`docker-compose.yml` 起两个服务：`db`（postgres:16-alpine，带健康检查）与 `api`（构建本仓库镜像，待数据库就绪后启动）。`DATABASE_URL` 已在 compose 中指向内部 `db` 服务，LLM / 检索密钥从根目录 `.env` 注入。数据库使用 `pgdata` 卷，前端设置中心的运行时配置使用 `appdata` 卷，容器重建后仍会保留。

安全默认值：容器以非 root 用户运行；`db` 不向宿主机发布端口；`api` 仅绑定 `127.0.0.1`，对外访问请经反向代理（TLS/限流）。生产建议在 `.env` 中设置 `API_KEY` —— 设置后所有 `/api` 端点要求 `X-API-Key` 请求头（或 `?api_key=` 参数，供 EventSource 使用）。前端无需手改：首次遇到 401 会自动弹出**密钥登录**，输入后密钥存于浏览器 `localStorage`；也可点导航栏「🔑 密钥」随时设置 / 更换 / 清除。

Nginx 反向代理可从 `docker/nginx.conf.example` 起步。SSE 实时进度要求关闭 `proxy_buffering`，并把读写超时提高到覆盖最长研究任务；若不用反向代理、明确要直接暴露端口，可在 `.env` 设置 `APP_BIND=0.0.0.0`，但仍应在安全组中限制来源并配置 HTTPS。

### 服务器端口与配置清单

推荐生产拓扑：公网 → Nginx/Caddy（HTTPS）→ `127.0.0.1:8000` → API 容器，API 容器通过 Docker 内部网络访问 PostgreSQL。

| 端口 | 是否开放公网 | 用途 |
|------|--------------|------|
| `80/tcp` | 是 | HTTP 与 HTTPS 跳转；若只使用 443 可按实际策略关闭 |
| `443/tcp` | 是 | Web 页面、API 与 SSE 实时进度 |
| `8000/tcp` | 否 | FastAPI 宿主机回环端口，仅供反向代理访问 |
| `5432/tcp` | 否 | PostgreSQL，仅在 Compose 内部网络访问 |

服务器需要允许**出站 `443/tcp`**，用于访问 OpenAI 兼容模型端点、Tavily 检索服务和 GitHub。

部署时主要维护以下文件：

- `.env`：服务器私有配置，不提交 Git。至少设置 `POSTGRES_PASSWORD`、`LLM_API_KEY`、`TAVILY_API_KEY`，生产环境建议设置 `API_KEY`。
- `docker-compose.yml`：应用、PostgreSQL、数据卷和端口绑定。
- `docker/nginx.conf.example`：Nginx 反向代理与 SSE 长连接示例；复制到服务器 Nginx 配置目录后修改域名。
- `.env.example`：环境变量模板，不包含真实密钥。

服务器拉取与启动：

```bash
git clone https://github.com/Lxiny-zy/deep-research-agent.git
cd deep-research-agent
cp .env.example .env
# 编辑 .env，填写口令与 API Key
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8000/healthz
```

## 持久化 · 历史与回放

经 API 提交的每次研究全过程会落库：计划、子问题、结果与发现、来源、报告、事件流（瞬态 token 事件不落库）。仓储抽象成 `ResearchRepository` 协议，两份实现行为对齐、可互换：

- **InMemoryRepository**：纯进程内存，离线单测零依赖，亦为 `ResearchRepository` 的参考实现。
- **SqlRepository**：async SQLAlchemy 2.0，本地 SQLite、生产 PostgreSQL 通用；API 默认使用。

API 由环境变量 `DATABASE_URL` 选择 SqlRepository 后端（缺省 `sqlite+aiosqlite:///./deep_research.db`）；CLI 直跑直出、不落库。配套 API 端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/runs` | 提交研究（后台执行），返回 `run_id` |
| `GET`  | `/api/runs` | 历史列表（分页 `limit`/`offset`，可按 `status`/`q`/`tag` 筛选） |
| `GET`  | `/api/runs/{id}` | 单次详情（计划 + 结果 + 报告 + 标签） |
| `DELETE` | `/api/runs/{id}` | 删除单条（进行中返回 409）；级联清子表 |
| `POST` | `/api/runs/batch_delete` | 批量删除（body `{ids:[...]}`，跳过进行中） |
| `PUT`  | `/api/runs/{id}/tags` | 设置标签（替换语义，body `{tags:[...]}`） |
| `GET`  | `/api/tags` | 全部标签 + 引用计数 |
| `GET`  | `/api/runs/{id}/events` | 事件回放（支持 `after_seq` 增量） |
| `GET`  | `/api/runs/{id}/stream` | SSE：进行中实时推送，已结束则从库回放 |
| `GET`  | `/api/config` | 当前全局配置（密钥脱敏） |
| `PUT`  | `/api/config` | 更新并持久化全局配置（对后续 run 生效） |
| `GET`  | `/api/research?q=` | 无持久化的即跑即看快路径（向后兼容） |

## 全局配置 · 前端设置中心

前端「设置」页（`GET`/`PUT /api/config`）可在线修改 LLM 模型 / Base URL / API Key、Tavily Key 与研究行为默认值，**改完即持久化、对后续创建的研究生效**——无需改 `.env` 或重启。

- **加载顺序**：环境变量（基础默认）→ `runtime_config.json`（前端写入的覆盖项）→ per-run `params`（本次运行覆盖）。
- **密钥安全**：`GET` 只脱敏回显（`…末四位` + 是否已设置），表单留空＝保持不变（不会被回写清空）；端点受 `API_KEY` 鉴权保护。
- **持久化位置**：默认写当前工作目录 `runtime_config.json`（已 gitignore），可经 `RUNTIME_CONFIG_PATH` 改路径。Docker Compose 已自动设置为 `/app/data/runtime_config.json` 并挂载 `appdata` 数据卷，容器重建后不会丢失。
- `database_url` 与服务端 `api_key` 不可经前端改（自举 / 鉴权安全），仍只来自环境变量。

## 数据库迁移（Alembic）

schema 版本由 Alembic 管理，`alembic/env.py` 复用 `Settings.database_url`（与应用同一配置来源）：

```bash
make migrate            # = alembic upgrade head，应用到最新版本
make revision m="说明"  # = alembic revision --autogenerate，按模型变更生成迁移
```

> API 启动时会为本地 SQLite 准备 schema：新库/已有 Alembic 库走迁移，历史 `create_all` 旧库会补齐新增验证列并 stamp 到当前 head；单测仍可直接用 `create_all` 快速建临时库。生产 PostgreSQL 用 `alembic upgrade head`（容器 entrypoint 已自动执行）。

## 自动化评估

```bash
python -m eval.run_eval
```

对内置用例集逐条研究并由 LLM-as-judge 打分，输出四维 + 均分汇总表。

## 测试

```bash
pytest            # 使用假 LLM / 假检索，无需任何密钥或联网
```

## 设计决策（面试可展开）

- **为什么结构化输出不用 provider 私有 API？** 为了 provider 无关与可移植：靠「Schema 注入 prompt + 稳健 JSON 抽取 + 失败重试」实现，任意兼容端点都能跑。
- **为什么把 Tracer 做成事件总线？** 同一份事件流，CLI 用来打印、API 用来 SSE，前端用来实时可视化——一处产生、多处消费。
- **为什么依赖注入？** Orchestrator 接受外部传入的 LLM / 检索后端，测试时注入假实现即可全离线跑完整流程。
- **并行与限流**：`asyncio.gather` 做 fan-out，`Semaphore` 限制并发，单个 Researcher 失败被隔离，不拖垮全局。

## Roadmap

- [x] 报告流式逐字输出（token streaming）
- [x] 子问题之间的依赖编排（DAG，而非纯并行）
- [x] 研究历史持久化 + 事件回放（SQLite / PostgreSQL，Alembic 迁移，Docker 一键起）
- [x] 实时统计（耗时秒级跳动 + token 随阶段累加，不止结束时一次）
- [x] 报告导出（Markdown 下载 / 复制）
- [x] 历史管理（删除单条·批量 / 状态·关键词·标签筛选 / 打标签分类）
- [x] 前端全局设置中心（模型 / 端点 / 密钥 / 检索参数，持久化生效）
- [ ] 检索后端增加 Bing / SerpAPI / 自建向量库
- [ ] 评估接入 LangSmith / Phoenix 做 tracing 看板
- [ ] 多用户与鉴权、报告 PDF 导出 / 分享链接
