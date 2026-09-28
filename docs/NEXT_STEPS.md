# 后续任务清单（2026-09-28）

## 现状

工作台改造的功能已经齐了，本地质量门全部通过，离线全链路也跑通了。但**还没有提交过，也还没有用真实模型跑过任务**。

| 检查项 | 结果 |
|---|---|
| 后端测试 `pytest -m "not pg"` | 1544 通过，覆盖率 88%（CI 门槛 80%） |
| `ruff check` / `ruff format --check`（CI 范围） | 通过 |
| `mypy deep_research` | 通过 |
| 前端 `tsc` / `eslint` / `vitest` / `build` / `api:check` | 通过，vitest 276 个 |
| 功能覆盖 `check_workbench_coverage.py` | 66/66（6 项不适用） |
| PostgreSQL 测试 `pytest -m pg` | 16 通过；0029/0030 升级 → 降级 → 再升级往返通过（临时容器 postgres:16，用后已删除） |
| 离线端到端 | 已跑通：`DR_DEMO_FAKE_BACKENDS=1` 的独立实例（端口 8001，数据放在临时目录）完成一次课题调研，并检查了运行页 |
| 真实模型端到端 | **未跑**：按你的要求暂不调用。当前配置 `gpt-4o-mini`，base_url 为空，走 OpenAI 官方端点 |
| Git | 最后一次提交在 9-21；之后约 190 个文件的改动都只存在于工作区 |

## 进度日志

状态标记：[x] 完成　[~] 进行中　[ ] 未开始　[!] 阻塞，需要你处理

- 2026-09-28
  - [x] 积分计费彻底移除，涉及代码和两份文档。覆盖脚本中积分计为「不适用」，不适用项变为 6 项
  - [x] 附件上传和模型阅读写进 RESEARCH_WORKBENCH.md
  - [x] PG 测试：16 个通过，迁移往返正常
  - [x] ruff 加入 `extend-exclude`，排除资料包和 `framework/run.py`。现在根目录 `ruff check .` 只剩两个临时脚本的报错
  - [x] 首页改版
    - 任务类型改成一行胶囊单选，说明和交付格式只显示当前选中的那一项
    - hero 压缩成两行，研究问题和附件区进入 1440×900 的首屏
    - 手机宽度下自动换行
    - vitest 272 个全部通过
  - [!] 删除 `.tmp_shots.py` 和 `.tmp_w.py`、修改 `.gitignore`：被权限拦截，需要你手动执行（命令见 P0 第 1 项）
  - [x] 补低覆盖模块的测试（P3），新增 35 个
    - `library/api.py` 66→95%，`library/ingestion.py` 70→94%：SSRF、重定向和大小上限
    - `llm.py` 69→93%：退避重试、解析回灌和 token 记账
    - `worker.py` 65→92%：`--check` 健康探针
    - `tavily_search.py` 46→100%，`report/capabilities.py` 0→100%，`init_db.py` 0→87%
  - [x] README 快速开始改写成工作台启动方式，补上附件说明和非 OpenAI 端点配置
  - [x] ErrorPage 补测试。`LiveTelemetryPreviewPage` 只在开发环境注册，是零消耗的动效预览，保留
  - [x] 对比度（WCAG AA 正文 4.5:1）
    - 亮色 `--text-3` 原来是 4.1，现在 ≥4.8
    - `--text-muted` 在 31 处用作文字，原来亮色只有 2.4、暗色 2.9，现在两套主题都 ≥4.5
    - 这只是 token 层面的机械检查，完整的无障碍结论还需要配合读屏工具做人工测试
  - [x] 离线端到端发现并修复了两个问题
    - 证据卡片用 `claim_id` 作 React key，同一句原文被多个子问题抽出时会重复，导致卡片可能被吞掉。已改为带序号，并加了回归测试（把修复撤掉测试就会失败）
    - 步骤轨道把 `attachment_reader` 和 `research_writer` 显示成了内部 ID。已补中文名，并新增 `test_role_labels.py`，保证所有注册角色在前端都有对应标签
  - [x] 运行页在 1440、1280、900、390 宽度下都没有横向溢出，三栏会逐级降级

## P0 先落地，别丢工作

1. **整理工作区**
   - [!] 删掉临时文件：`.tmp_shots.py`、`.tmp_w.py`，可以在输入框执行 `! rm .tmp_shots.py .tmp_w.py`。
   - 下面三项需要你确认用途后再处理：`.reasonix/`、`deepseek_markdown_20260926_b4e134.md`、`apevon-full-package.zip`。
   - 把 `apevon-full-package/` 和它的 zip 加进 `.gitignore`。这是第三方产品的提示词和技能包，不应该进仓库。CI 在目录不存在时会自动跳过覆盖检查（`ci.yml:72`），所以忽略它不影响 CI。
2. **分批提交**，放在当前分支或新开一个 `feat/research-workbench` 分支，建议拆成：
   - 后端工作台（`deep_research/workbench/`、`plan_contract`、`plan_handoffs`、`tools/fanout.py`）
   - 资料库和两个迁移（`library/`、`alembic 0029/0030`）
   - 报告交付（`report/bundle.py`、`latex.py`、`templates.py`）
   - 前端重设计（侧边栏、新页面、样式、旧样式归档）
   - 文档和覆盖脚本
3. [x] **跑 PG 测试**：已完成，见进度日志。

## P1 真实验收（最能暴露问题）

mock 测试证明的是流程连得通，结果好不好只有真实调用才看得出来。当前配置是 `gpt-4o-mini` + Tavily。每项跑一次，记录耗时、token、质量门结论：

| 任务 | 验收要点 |
|---|---|
| 课题调研（深度） | 引用数达到综述下限（20），逐字核验通过率，反思补洞有没有真的补上缺口 |
| 课题调研（快速） | 耗时与 token 明显低于深度档 |
| 文献综述 + 上传 PDF | 附件片段被引用，参考来源显示「文件名 · 第 N 页」 |
| 论文精读（arXiv 链接） | 全文解析成功，按章节定位 |
| 数据分析（上传 CSV） | 图表生成，统计结论与数据一致 |
| 幻灯片 / 思维导图 | PPTX、导图能打开，地名规范门通过 |
| 学术问答多轮 | 上下文延续、引用可点 |

发现的问题回填成回归测试（fixture 用录制的真实响应，不要调用在线服务）。

## P2 前端收尾

- [x] **首页布局**：输入区已进入首屏，任务类型改为紧凑排列。
- [~] **运行页**：用离线数据检查过「已完成 / 部分完成」两种状态。长报告和失败步骤还要等 P1 的真实运行记录。
- [x] **窄屏**：1280、900、390 宽度都已检查。
- [~] **无障碍**：对比度已修。键盘可达性和读屏需要人工测试。
- [x] ErrorPage 测试已补。预览页保留（只在开发环境加载）。

## P3 质量与维护

- [x] **补低覆盖模块的测试**：见进度日志。`capabilities.py`（`/api/capabilities` 在用）和 `init_db.py`（容器建表入口）都还在用，补了测试，没有删除。
- [x] **根目录 `ruff check .` 会报 245 个错**，全部来自 `apevon-full-package/` 和 `framework/run.py`。在 `pyproject.toml` 里把它们加入 `extend-exclude`，免得本地误报。
- [x] **CRLF 问题**：仓库已经有 `.gitattributes`，索引中 493 个文件全部是 LF。提示来自本机的 `core.autocrlf=true`，不影响提交内容，不用处理。
- [x] **README**：快速开始已改写。

## 暂不做

- 积分和订阅计费：已经明确不做。
- GPU 调度：只调用云端 LLM，不做（见 `RESEARCH_WORKBENCH.md`）。

## 建议顺序

P0（半天）→ P1（跑 7 个真实任务，按问题修复，1–2 天）→ P2 首页改版，并借真实数据检查运行页 → P3 按时间穿插。
