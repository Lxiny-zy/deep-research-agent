# AI4S 科研深度检索引擎 · 执行台账与后续方案

> 更新于 2026-08-25。本文件是**唯一的进度权威**：已执行的每一项写清"改了什么 /
> 为什么这么改 / 在哪 / 验证到什么程度"，待执行的写清依赖顺序与设计要点。
>
> 领域分析与最初的方案推导见 [AI4S_HSI_PLAN.md](AI4S_HSI_PLAN.md)（§0–§7 仍然有效）。

## 0. 目标与边界

把仓库从**通用深度调研系统**改造成面向**高光谱计算成像**（DOE / CASSI，光学编码 →
计算重建全链路）的 **AI4S 科研向深度检索引擎**。

三条已拍板的边界：

1. **只读文献**，不挂科学计算工具；
2. **学科锁定高光谱计算成像**；
3. **必做全文解析**，证据要能逐字落到 Results / Methods 段。

**保证什么**：出处可追溯到 DOI、引用可逐字核验、数值可逐位核对、单源/双源/冲突
状态可判定、每个表格数值可回溯到原文段落与内容哈希。
**不保证什么**：论断在开放世界为真。这个口径不随功能增加而放宽。

---

## 1. 已执行

本轮已执行批次均带测试并已落地；以下记录以代码和验证结果为准。

### 1.1 学术检索源与 DOI 级出处

| 项 | 位置 |
|---|---|
| OpenAlex 后端（DOI / 作者 / 机构 / 期刊 / 撤稿标记 / OA 全文位置 / 倒排索引摘要还原） | `deep_research/tools/openalex.py` |
| arXiv 后端（Atom 解析、版本剥离、XML 失败关闭） | `deep_research/tools/arxiv_search.py` |
| 学术引用渲染（单点实现） | `deep_research/citation.py` |
| `ScholarlyMetadata` / `Source.scholarly` / `EvidenceVerification.source_reference` | `models.py` |
| 后端白名单与礼貌池邮箱 | `config.py`、`.env.example` |
| 参考来源列表升级为 DOI 级 | `agents/synthesizer.py` |
| 前端引用回退解析器适配新行格式 | `frontend/src/lib/evidence.ts` |
| 迁移 | `alembic/versions/20260824_0020_scholarly_provenance.py` |

关键决定：

- **引用在验证时刻渲染并落库**。只有那一刻同时握有 Finding 与 Source，所以历史回放
  与 worker 跨进程执行拿到的引用完全一致。
- **`peer_reviewed` 三态**。OpenAlex 按 `primary_location.version` 推断；缺失落 `None`。
  把未知压成 `False` 会让报告给出自己并不掌握的结论。
- **arXiv 记录一律 `peer_reviewed=False`**。取回的那份文档本身就是预印本，与"是否另有
  已发表版本"是两件事。
- **`Report.citations` 保持纯 URL 列表**，前端 `[n]` 跳转与快照覆盖率指标的契约不变。

### 1.2 结构化报告文档（三格式地基）

| 项 | 位置 |
|---|---|
| 块模型（Prose / Table / Chart）、证据记录、参考条目、概览、免责声明 | `deep_research/report/document.py` |
| 内联 SVG 图表（bar / dot / grouped_bar / scatter / line） | `deep_research/report/charts.py` |
| Markdown 投影（最保守子集） | `deep_research/report/markdown.py` |

**核心不变量：图表必有源表。** `ChartBlock` 不携带任何数据字段，只携带 `source_table`
与取哪几列。于是"凭空画一张图"在**类型层面**不可表达；图上每个点都来自通过门禁的
单元格，都带 `[n]`；MD 渲染不了矢量图，但源表结构上必然存在，降级无损。

一张编造的柱状图比一句编造的话危险得多，因为它看起来是"数据"。把这条做成结构性质
而不是流程约定，判定器被绕过也不会失效——与"意图判定只能收紧"同一思路。

其他落地口径：

- **柱状图零基线 vs 点图**。柱长即数值，基线必须为零；要看 35 dB 基座上的 0.5 dB
  差异就换 `dot`（位置编码没有"从零开始"的语义），而不是截断 Y 轴。
- **量纲不同的多系列被拒绝**。实测 PSNR(31–38 dB) 与 SSIM(0.89–0.97) 并排时 SSIM
  渲染成 8px 残根——双 Y 轴撒谎的变体。
- **中文标签按显示宽度裁剪**（全角计 2）。24 个汉字在 12px 下约 288px，标签区只有 140px。
- **零依赖**。刻意不用 matplotlib：它带 numpy + freetype，且默认无中文字体。
- **交互不靠 JS**。每个标记内嵌 `<title>`，自包含导出与打印都保留。

### 1.3 三格式导出与打印

| 项 | 位置 |
|---|---|
| 服务端规范化装配（原先只在前端 TS 里的 join） | `deep_research/report/assemble.py` |
| 结构化文档端点 | `GET /api/runs/{id}/document` |
| 打印样式表 | `frontend/src/print.css` |
| 可打印报告组件 | `frontend/src/components/PrintableReport.tsx` |
| 导出按钮（复制 / .md / 打印预览 / 打印·存为 PDF） | `frontend/src/components/ReportActions.tsx` |

- **修掉了"Ctrl+P 只印第一屏"**。`design-system.css` 的 `.report-view-body` 是滚动容器，
  滚动容器打印时只输出可视区域。`print.css` 显式解掉所有相关容器的
  `overflow` / `height` / `position`。
- **侧栏是"替换"不是"隐藏"**。屏幕上装置按需呈现（点 `[n]` 一次看一条），纸上没有
  按需，所以全部证据铺成尾部附录并另起一页。`display:none` 掉侧栏等于装置消失。
- **tooltip-only 信息提升为正式内容**。验证理由、语义置信度、印证说明、完整哈希原先
  只活在 `title=` 里——触屏不可达、打印不输出，在 HTML 移动端就已经在无声丢失。
- **PDF 走浏览器打印**。打印对话框本身就是分页预览 + 打印机 + 另存为 PDF 三合一，
  零依赖、桌面版打包零风险。另加应用内 A4 版心预览（**分页是模拟的**，真实分页由
  浏览器分页器决定）。

### 1.4 结构化数值与确定性数值校验

| 项 | 位置 |
|---|---|
| 数值解析 / 单位归一化 / 容差 / 支持判定 | `deep_research/quantities.py` |
| `Quantity` / `ExperimentConditions` / `quantity_status` | `models.py` |
| 接入证据门禁与准入规则 | `guardrails.py` |
| 迁移 | `…_0021_finding_quantities.py` |

逐字校验回答"这句话在不在原文里"，对以下三种**完全无感**（被改的是数值与单位的
对应关系，不是措辞）：

1. 引文抄对但数字抄错一位（38.36 → 3.836）；
2. 引对一句同时提到 PSNR 与 SSIM 的话，把 SSIM 的 0.967 报成 PSNR；
3. 把"超过 35 dB"变成"等于 35 dB"——比较符被吞掉，下界变成点值。

所以数值校验是独立的确定性门禁，**纯代码不调 LLM**。

- **容差按有效位数**：`38.36` → ±0.005，`38.4` → ±0.05。这恰好把"报告降低精度"
  （38.36 → 38.4 通过）与"抄错"（38.36 → 38.3 拒绝）分开。
- **单位不符有独立原因码** `unit_mismatch`，并写明原文实际是什么。
- **不做 `%` ↔ 无单位换算**：SSIM 有报 `0.948` 也有报 `94.8%` 的，自动换算会抹掉
  "单位不符"这个真实信号。
- **三态向后兼容**：未声明数值 → `not_applicable`，不影响准入；声明了却对不上 →
  `unsupported`，拒入报告。此时逐字状态**仍是 `verified`**，两个门禁各自独立判定，
  审计能看出"引文对但数字错"这种具体情形。

### 1.5 表格透视

| 项 | 位置 |
|---|---|
| 透视器 | `deep_research/report/pivot.py` |
| `Finding.entity`（表的"行"） | `models.py`、`agents/researcher.py`、迁移 `0022` |
| 多引用单元格 + 列头脚注 | `report/document.py` |
| 自动接入装配 | `report/assemble.py`（`include_tables`，默认开） |

- **行是"对象"不是"论文"**。一篇 CASSI 论文常同时报自己与多个 baseline 的数字，
  按论文分行会把它们压进一格。
- **口径不同的数值分列**。按 `(指标, 条件签名)` 建列，列头各自指向口径脚注。
  **"未标注口径"自成一列**——"不知道条件"不等于"条件相同"。
- **冲突并列但排除出图表**。表里并列 + 标 `⚠`，不静默挑一个；该格 `numeric` 置空，
  因为把有争议的值画成一个点等于替读者裁决，而图上看不出那里有分歧。
- **向后兼容**：历史 findings 没有 `entity` / `Quantity`，透视器返回空列表，既有产物
  逐字节不变，所以自动开启是安全的。

HSI 四表的结构化抽取字段已经接入正式链路：`ExperimentConditions` 现在保留光谱范围、
场景、采集方式、标定、原型验证、编码方式和色散元件；Researcher 只从逐字引文抽取这些
字段，条件签名会把它们分别纳入可比性分列。参数量、FLOPs、推理时间等资源指标也会
映射到重建表；缺失字段继续显示为未报告。

### 1.6 发布方独立性（修伪双源）

| 项 | 位置 |
|---|---|
| 身份归一化与并查集聚类 | `deep_research/independence.py` |
| `SourceIdentity` + `EvidenceVerification.source_identity` | `models.py` |
| 接入交叉印证门禁 | `guardrails.py` |
| 指标同口径 | `reproducibility.py` |
| 迁移 | `…_0023_finding_source_identity.py` |

**这是本轮唯一一处修正"会给出错误结论"的逻辑。** 原先按 registrable domain 判独立
发布方，文献场景下：一篇工作同时存在 arXiv 预印本 + 期刊正式版 + 机构库副本 = 三个
域一篇工作 → 被算成"三个独立来源" → "已交叉印证"这个结论本身是假的。

判定单位改为**团队**：同一篇工作 → 1；不同工作同一团队 → 1；不同工作不同团队 → 2。

五条合并规则（任一命中即同一发布方）：同一 DOI、同一 work_id（剥版本号）、同一
归一化标题、**作者重叠**、同一 registrable domain。

两处设计要点：

- **用并查集而不是分组键**。独立性由"重叠关系"定义而非相等关系：A–B 共享一位作者、
  B–C 共享另一位 → A/B/C 同簇。传递闭包在极端情况下会过度合并，但那是失败关闭。
- **规则 5 保留旧行为作为下界**，因此新逻辑的独立来源数**恒不大于**旧逻辑（单调性，
  有测试断言）。这意味着本次改动在结构上不可能放宽门禁，**不依赖任何一条规则的
  正确性**。
- **刻意不做机构重叠**：同一机构不等于同一团队，大学里两个互不相关的组报同一个数
  是真的独立验证。"同一团队"这个真正的信号已由作者重叠覆盖。
- **旧记录退回只按域名**，不借 `source_title` 做局部升级——本项目把"判定可从存下来的
  输入复现"当硬性质。
- **伪双源不再静默**：被驳回的印证对写进 `corroboration_reason`（`同源来源被驳回(same_doi)`），
  否则读者以为系统只找到一个来源。

---

## 2. 验证状态

| 项 | 状态 |
|---|---|
| 后端功能测试 | **1088 passed / 3 skipped**（`pytest -q --ignore=tests/test_migrations_pg.py --ignore=tests/test_migrate.py`） |
| 前端测试 | **170 passed**（21 个文件） |
| ruff check / format | 全清（170 个 Python 文件） |
| mypy | 全清（89 源文件） |
| `vite build` | 成功 |
| PostgreSQL/Alembic 迁移 | **未纳入本轮功能验收**（按当前任务范围暂不执行） |

本轮新增测试：`test_scholarly_backends`(35)、`test_arxiv_fulltext`(13)、`test_report_document`(35)、
`test_report_assemble`(17)、`test_quantities`(30)、`test_report_pivot`(19)、
`test_independence`(36)、`test_corroboration_gate`(+5)、`test_api`(+3)、
`PrintableReport.test.tsx`(14)、`evidence.test.ts`(+2)。
`SettingsPage.test.tsx`(2)。另有 HSI 意图/策略/workflow 定向用例（124）、CSV 投影用例（3）及全文运行时配置
API 回归已纳入上方总数。
本轮追加 `test_intent_policy_runtime.py`（HSI Planner 契约与策略收紧）、HSI draft fixture
provenance 校验，以及四个文档导出端点的 `include_hsi_tables`/`table_id` 转发与错误码回归。

### 部署前必做

```bash
# 阿里云服务器上（生产用 PostgreSQL，见下）
DATABASE_URL=postgresql+asyncpg://dr:<pwd>@db:5432/deep_research \
  pytest tests/test_migrations_pg.py
```

生产数据库配置已确认为 PostgreSQL：`docker-compose.yml:48` 固定
`postgresql+asyncpg://dr@db:5432/deep_research`（覆盖 `.env`）；`db` 为
`postgres:16-alpine` 且不向宿主机发布 5432；`config.py:228` 在 `APP_ENV=production`
下**启动即失败**如果 URL 不以 `postgresql+` 开头；`Dockerfile` entrypoint 先
`alembic upgrade head` 再起 uvicorn。

---

## 3. 已知限制（不假装解决）

| 限制 | 说明 |
|---|---|
| **benchmark 数值传抄** | 论文 B 从论文 A 的表里抄 baseline 数字，两篇不同工作、不同团队，判为独立——但 B 不是独立测量，只是转录。元数据里没有信号能区分，所以不猜。"≥2 个独立发布方"仅指两个独立团队各自发表了这个说法，**不等于两次独立测量** |
| OpenAlex 每日配额 | 约 1000 次/日按出口 IP 计，UTC 午夜重置。共享出口 IP 会被提前耗尽（本机实测即已耗尽）。配额耗尽抛 `OpenAlexQuotaExceeded` 并带重置时间；多后端下被隔离 |
| 浏览器打印无自定义页眉页脚 | Chrome/Edge 不支持 `@page` margin box，`position:fixed` 只在首页渲染。页码只能用浏览器自带那套 |
| 页底脚注做不了 | `float: footnote` 无支持；证据装置本就是正文的 3–8 倍体积，只能走尾注附录 |
| 打印预览分页是模拟的 | 真实分页由浏览器分页器决定，可能差一两行 |
| 作者名罗马化变体 | `Cai` vs `Tsai` 这类变体判不出同一人 → 漏合并 → 失败开放 |
| 缩写姓氏 | `Y. C.` 与 `Yuanhao Cai` 归一到不同键 |
| 两处装配并存 | Python 负责 `.md` / `/document` / 服务端导出，TS 负责交互视图与浏览器打印 DOM，属于不同渲染出口。前端 API 边界已通过 `normalizeReportDocument` 归一化新版结构化文档、旧版 `Report` 和不完整响应；无法识别的响应回退旧 `RunDetail`。两套渲染器仍分别保留，但数据兼容已完成 |

---

## 4. 后续执行方案（按依赖顺序）

### 4.1 撤稿硬门禁（已完成）

撤稿标记已接入证据身份与报告准入：`SourceIdentity.retracted` 保留三态，
`report_eligible` 只拒绝显式 `True`，未知 `None` 与显式 `False` 不误杀。
`EvidenceVerifier` 在验证时复制该标记，并把 `source_retracted` 写入验证理由；
引用仍保留 `【已撤稿】` 展示，撤稿证据只能出现在审计/附录，不会进入正文素材。
变更位置：`models.py`、`guardrails.py`、`reproducibility.py`；回归测试在
`tests/test_guardrails.py`。

### 4.2 全文解析（arXiv LaTeX + OA PDF 已完成）

目标：证据从摘要级进到 Results 级。这是最初三条边界里唯一还没兑现的一条。

**唯一的硬约束在 prompt 体积**：`agents/researcher.py` 把所有 `source.content` 原样拼
进 user prompt。当前摘要 1–2k 字符，一篇 CASSI 论文全文 40–60k。全文不能整篇进
prompt，必须**节级筛选**后再送——这是本步最实质的重构。

分两条路，成本不同：

| 路径 | 依赖 | 覆盖 |
|---|---|---|
| **arXiv LaTeX e-print** | **零**（stdlib `tarfile` + `re`） | 重建算法侧绝大部分文献；**表格是 `\begin{tabular}` 结构化文本**，比 PDF 抽取无损 |
| OA PDF（Optics Express 等光学侧） | PyMuPDF（可选依赖 `pip install -e ".[fulltext]"`） | 光学编码侧 |

arXiv LaTeX 与 OA PDF 均已落地。PyMuPDF 按需懒加载；未安装时服务仍可启动，全文获取失败会回退摘要。

已完成位置：`deep_research/tools/arxiv_fulltext.py`、`arxiv_search.py`、
`config.py`、`execution.py`、`agents/researcher.py`。解析器对 tar 路径穿越、链接/设备文件、
成员数与大小、递归 include 做上限和失败关闭；按查询确定性筛选节，保留表格与数值文本。
arXiv 来源按节生成带 `dr_section=*` 的来源片段，`ScholarlyMetadata.section` 和证据附录
记录节名；下载/解析失败回退 Atom 摘要，不宣称全文覆盖。`FULLTEXT_ENABLED` 与
`FULLTEXT_MAX_CHARS` 纳入运行 checkpoint，可在部署时关闭或调节预算；同时已加入
`/api/config` 与设置页的持久化开关/预算输入（只影响之后创建的 run）。
已知节名下，结构化数值来自 Abstract / Introduction / Method / Conclusion 时会被数值门禁
标为 `quantity_section_not_allowed`；Results / Experiment（含表格文本）才可进入报告。

配套：

- **分节**：Abstract / Introduction / Method / Experiment / Results / Conclusion；
- **按节限定证据取用**——比"证据在不在全文里"更有意义：数值论断只接受 Results 或
  表格的引用，方法描述只接受 Method 段。这条直接提升 `quantity_status` 的可信度；
- **节级筛选**：按子问题用确定性 BM25-like 评分选相关节，只把选中的节送进 Researcher；
- `ScholarlyMetadata.section` 已从预留字段变成实际证据出处。

### 4.3 `hsi_review` 工作流（已完成）

```
检索策略生成（子问题 → 各库检索式 + 同义词扩展）
  → 多库并行检索 → 同 work 去重（复用 independence.cluster_sources）
  → 筛选（纳入/排除，逐篇记录排除理由）
  → 全文获取与分节 → 结构化抽取（entity + quantity + conditions）
  → 冲突检测 → 表格透视 → 综合 → Critic
```

已落地位置：`deep_research/workflows.py`、`deep_research/intent/types.py`、
`intent/rules.py`、`intent/routing.py`、`agents/intent_router.py`、`workflow.py`。
`hsi_review` 复用 deep 的 Planner → Researcher → Reflector → Synthesizer 链，追加
Critic；五个 HSI 意图均有独立规则、路由和执行策略，策略快照进入 checkpoint，自动
收紧子问题/并发/双源门禁，显式 workflow 仍优先。定向分类、策略和 workflow 测试已覆盖。

产出天然带"检索 N 篇、纳入 M 篇、排除理由分布"这种系统综述式叙述，而 run manifest
与检索快照（`reproducibility.py`）已经是它的底座。

新意图标签接现有三级级联：`literature_review` / `method_comparison` /
`benchmark_survey` / `reproducibility_check` / `dataset_discovery`。按 `intent/types.py`
的原则，**每个标签都必须落到下游动作上**，落不到就不设。

### 4.4 领域评测集（代码完成，真实样本 draft 已接入）

评测代码 `eval/hsi_benchmark.py` 已完成，量化：

- 数值抽取准确率（`quantity` 与论文原表比对）；
- 实验条件抽取完整率；
- 伪双源拦截率（构造 arXiv + 期刊同一工作的用例）；
- 表格透视的列划分正确率（口径是否被正确分列）。

当前 fixture `eval/baselines/hsi_gold.json` 已替换为一条从 DAUHST 公开 arXiv
LaTeX 源人工核对的 `curated_draft` 样本（Results 中的 38.36 dB / 0.967 SSIM，
KAIST 10 场景、28 波段、CAVE 训练集的 256×256 training patches），并保留 arXiv URL、DOI、
章节和逐字证据句。
它仍然不是生产金标准：需要第二名标注者复核，并继续加入不同论文、真实光学编码方案和
跨论文表格样本后，才能用于发布门禁。测试只验证结构化匹配与 provenance，不把该单篇
样本的分数当成领域准确率。`load_hsi_gold()` 对 `curated_draft` 做确定性 provenance
完整性校验（来源 URL、章节、逐字引文、数值/指标均必须在引文中出现），但不会把联网抓取
结果冒充人工复核。

### 4.5 输出侧补齐（可并行，均较小）

| 项 | 说明 |
|---|---|
| CSV 导出（已完成） | `deep_research/report/csv.py` 直接投影 `TableBlock`；`GET /api/runs/{id}/document.csv` 支持 `table_id`，保留缺失值、引用、脚注和争议标记 |
| XLSX 导出（已完成） | `deep_research/report/xlsx.py` 使用可选 `openpyxl` extra；`GET /api/runs/{id}/document.xlsx` 延迟加载依赖，缺失时返回 501，不影响服务启动 |
| 服务端 PDF（已完成） | `deep_research/report/pdf.py` + `GET /api/runs/{id}/document.pdf`；WeasyPrint 为**可选依赖**，缺它只返回 501，不影响服务启动。`python:3.11-slim` 不含 CJK 字体，生产镜像仍需安装 `fonts-noto-cjk` |
| 前端结构化文档接入 | 已完成终态接入：完成态优先消费 `/document`，结构化证据/引用/表格进入交互报告和打印视图；API 边界的 `normalizeReportDocument` 兼容旧版 `Report`、部分字段响应和滚动升级，非法响应触发旧 `RunDetail` 回退；流式阶段继续显示实时 Markdown。TS 交互/打印渲染与 Python 服务端导出继续按出口分工 |
| 四张领域表（代码 schema + 正式字段适配） | `deep_research/report/hsi_tables.py` 定义光学编码、重建算法、数据集/协议、证据强度四表；`Planner` 在 HSI 策略下动态注入同一 schema 契约；报告/API/CSV/XLSX/PDF 通过 `include_hsi_tables=true` opt-in 输出。实体、数值、实验条件、资源指标和来源三态已接入；后续只补充更多真实论文标注回归 |

---

## 5. 当前完整链路

```
学术检索（OpenAlex + arXiv，DOI 级出处，配额失败被隔离）
  → arXiv LaTeX e-print 安全解包与节级筛选（失败回退摘要）
  → 来源安全门禁（URL / 注入信号 / 意图审查）
  → 逐字证据校验（归一化匹配 + 内容哈希）
  → 语义支持判定（LLM，可校准）
  → 数值校验（纯代码：数值 + 单位 + 容差按有效位数）
  → 论断一致性与交叉印证（按"同一工作 / 同一团队"聚类判独立性）
  → 表格透视（口径分列 / 冲突并列 / 缺失如实）
  → 图表（结构上必有源表，不可凭空画）
  → Markdown / HTML / CSV / PDF 四格式 + 证据附录（含完整哈希与验证理由）
```

LLM 在这条链上只做三件事：拆子问题、抽取候选发现、写叙述。**它不判定证据是否
通过、不碰表格数据、不碰图表数据。**
## 6. Follow-up execution (2026-08-25)

本轮继续完成了四项收尾：

1. Planner 只在 HSI 策略下注入代码拥有的四表 schema 契约，包含缺失值、实验条件、冲突和
   同一 work/团队的证据要求；普通任务不受影响。
2. `hsi_gold.json` 已从合成数值替换为带 arXiv Results 逐字引文和条件 provenance 的
   单篇 `curated_draft` 样本，并明确不把它当成真实领域金标准。
3. `/document`、CSV、XLSX、PDF 四个出口新增 `include_hsi_tables` 参数转发回归，CSV/XLSX
   的 `table_id` 选择和 400/404 错误路径也已覆盖。
4. HSI 条件/资源字段、同行评审三态、同值多来源引用保留，以及前端 `/document` 失败回退和
   canonical HSI intent 覆盖已完成；结构化数值/条件也进入证据侧栏和打印附录。后端最终回归、
   前端测试和构建已重新执行（最终数字见上方验证表）。

仍待执行：继续补充第二名标注者和多篇真实论文的 HSI 数值/条件/伪双源/列划分回归。
PostgreSQL/Alembic 迁移验证不属于本轮功能模块验收。前端兼容适配已完成：终态报告优先消费
`/document`，旧版响应和无法归一化的响应安全回退 `RunDetail`；TS 与 Python 仍保留各自出口的
渲染职责，这不阻塞当前功能交付。
