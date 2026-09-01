# AI4S 改造方案：高光谱计算成像文献调研

> **进度以 [AI4S_STATUS.md](AI4S_STATUS.md) 为准**——那里是已执行台账与后续方案。
> 本文件保留最初的领域分析与方案推导（§0–§7 仍然有效），以及各批次落地时
> 记下的设计取舍（§8.5 起）。两者内容有重叠时以 STATUS 为准。

## 0. 定位与边界

把本仓库从**通用深度调研系统**改造成面向**高光谱计算成像**（光学编码 → 计算重建全链路）
的 **AI4S 文献调研 agent**。三条已拍板的边界：

1. **只读文献**。不挂科学计算工具，不做"能算"的形态（无 RDKit/pymatgen 类工具层）。
2. **学科锁定高光谱计算成像**。DOE、CASSI（SD/DD）、衍射旋转等光学编码方案，
   以及模型驱动 / 深度 / 深度展开 / 生成先验各类重建方法，含仿真与真实原型两类验证。
3. **必做全文解析**。证据必须能逐字落到论文的 Results / Methods 段，不停在摘要级。

**能保证什么**：出处可追溯到 DOI、引用可逐字核验、单源/双源/冲突/撤稿可判定、
每个表格数值可回溯到论文原文的具体段落。
**不保证什么**：论断在开放世界为真。README 第 23 行的口径不改，AI4S 版本同样不宣称
开放世界事实判定率。

## 1. 为什么现有架构能承接（以及唯一的硬约束）

**关键结论：全文只要灌进 `Source.content`，现有确定性逐字门禁自动就在全文上生效，
不需要改 `guardrails.py`。** `EvidenceVerifier` 拿 `(candidate, source)` 做归一化逐字
匹配（`agents/researcher.py:197`），匹配的对象就是 `source.content`。这是整个改造
成本可控的根本原因——证据链的骨架已经是对的，缺的是喂给它的东西。

**唯一的硬约束在 prompt 体积**：`agents/researcher.py:160-164` 把所有 `source.content`
原样拼进 Researcher 的 user prompt。当前 Tavily 摘要截断在 2000 字符（`tools/tavily_search.py:26`），
而一篇 CASSI 论文全文约 40–60k 字符。全文**不能**整篇进 prompt，必须做节级筛选后再送。
这是 Phase 2 必须动的地方，也是本次改造最实质的一处重构。

## 2. 检索层：学术源

新后端全部实现 `SearchTool` ABC（`tools/base.py:10`），由现有 `MultiBackendSearch`
并发合并去重（`tools/composite.py:69`）。`config.py:200` 的后端白名单需同步扩展。

| 源 | 拿到什么 | 为什么这个领域需要 | 密钥 |
|---|---|---|---|
| **arXiv** | 预印本元数据 + **LaTeX e-print 源码** + PDF | MST/CST/DAUHST 一类重建工作的首发地；LaTeX 源是表格的最优解（见 §4） | 无 |
| **OpenAlex** | DOI、venue、作者、**机构**、OA 全文位置、引用数、撤稿标记 | 元数据主干；机构信息是修独立性判定的前提（见 §5.2） | 无 key，但有每日免费配额（见下） |
| **Crossref** | 权威 DOI 解析、`update-to` 关系 | 撤稿/更正检测的一手来源 | 无（同上） |
| **Semantic Scholar** | 引用图（references / citations）、OA PDF 链接 | 窄领域必须能做引文追溯（snowball）；CASSI 谱系是链式演进的 | 免费 key（无 key 有限流） |
| **Optica（Optics Express 全 OA）** | 光学侧论文全文 PDF | CASSI 光学编码侧主阵地，且合法可取全文 | 无 |
| Tavily / Brave（保留） | 会议官网、代码仓库、非索引资料 | 兜底，不作为科学证据主源 | 已有 |

**不接**：PubMed（除非将来做生物医学 HSI）；IEEE Xplore 全文（付费、无开放全文 API，
只经 Crossref/OpenAlex 取元数据）。
**待验证**：Papers with Code 疑已归档/下线，**不作为依赖**。benchmark 榜单改用 NTIRE
挑战赛报告 + 论文原表抽取。

> **Phase 1 实测修正（2026-08-24）**：OpenAlex 已不是"无条件免费"。它按出口 IP 计
> **每日配额**（响应头 `x-ratelimit-limit` 约 1000 次/日，UTC 午夜重置，可付费加额）。
> 一次研究只消耗个位数请求，正常用量远够；但共享出口 IP 会被同 IP 的其他调用提前耗尽
> ——本机实测即为已耗尽状态，连 API 根路径都返回 429。
>
> 处置：配额耗尽抛 `OpenAlexQuotaExceeded` 并把重置时间写进错误信息（裸 429 让人不知
> 何时恢复）；多后端下 `MultiBackendSearch` 隔离该失败，其余后端照常产出。
> **这条也是把 arXiv 当第二学术源、而不是只依赖 OpenAlex 的实证理由**：
> arXiv 侧同一时刻实测正常，检索"mask-guided spectral transformer"直接召回
> DAUHST 与 MST 两篇，元数据（作者、年份、版本 v3、OA PDF）解析全部正确。

## 3. 出处层：`Source` 扩展

`models.py:29` 的 `Source` 增加科学元数据，全部可选、默认空，向后兼容既有 checkpoint：

```
doi, authors[], affiliations[], venue, year, version（arXiv v1/v2…）,
work_id（OpenAlex/S2 的同一工作聚类 ID）, peer_reviewed, oa_pdf_url,
retracted, citation_count, section（这段证据取自哪一节）
```

引用从 URL 升级为 DOI 级：`synthesizer.py:108` 的参考来源列表从 `[n] <url>` 变成
`[n] 作者. 标题. 期刊, 年. doi:xxx`。

## 4. 全文层：优先 LaTeX，其次 PDF

**这是本领域最关键的一处工程判断。** arXiv 提供 e-print 源码（含 `.tex`），而
**LaTeX 里的表格是 `\begin{tabular}` 结构化文本，PDF 表格抽取是有损的**。这个领域的
核心产物恰恰就是 benchmark 数值表，所以：**能拿 LaTeX 就绝不走 PDF 表格抽取。**

- **arXiv 论文**：抓 e-print 源码，解析 `.tex` 取正文分节 + `tabular` 取表格。
- **非 arXiv OA（Optics Express 等）**：PyMuPDF 取文本；GROBID 可选（TEI XML 自带
  章节结构与表格 caption），按解析质量决定是否引入这个额外服务依赖。
- **分节**：Abstract / Introduction / Method / Experiment / Results / Conclusion。
- **按节限定证据取用**——比"证据在不在全文里"更有意义的一条规则：
  数值论断只接受来自 Results 或表格的引用；方法描述只接受 Method 段。
- **节级筛选**（解 §1 的体积约束）：按子问题先用 BM25/embedding 选出相关节，
  只把选中的节送进 Researcher。`researcher.py:160-164` 的 context 组装据此改写。

## 5. 证据层：三项本领域专属加固

### 5.1 数值 + 单位 + 协议

科学论断的核心不是句子，是"**在什么条件下，某指标 = 某数值**"。`Finding`（`models.py:106`）
增加：

```
quantity: {value, unit, uncertainty}
conditions: {dataset, bands, spatial_size, mask, protocol, train_data}
claim_type: 实证结果 | 方法描述 | 综述观点
```

新增**确定性数值校验器**：从 `evidence_quote` 抽数值 + 单位，与 `statement` 中的数值
比对（含 dB / nm / µm 换算），不一致直接判 `unsupported`。纯代码，与现有逐字校验同源，
不依赖判定模型。

**为什么这个领域必须做**：`PSNR 34.26 dB` 这种数字在不同论文里对应不同协议——
28 波段 vs 31 波段、256×256 裁剪 vs 全图、mask 不同、训练集不同。抄了数字不抄协议
就是不可比，而不可比的数字放进同一张表就是误导。

### 5.2 独立发布方判定必须改（现有逻辑在本领域会出错）

`guardrails.py:427` 按 `_registrable_domain(finding.source_url)` 判独立发布方，
`464-469` 按去重后的域名个数计 `independent_source_count`。

**问题**：同一篇 CASSI 工作常同时存在 arXiv + Optics Express/CVPR + 机构 repo
三个不同域 → 被算成三个独立来源 → **伪双源**。不修，"双源核验"在本领域就是假的。

**改法**：
1. 先按 DOI / OpenAlex `work_id` / 标题模糊匹配**聚类成同一 work**，同一 work 只计一次；
2. 再按**作者与机构重叠**判独立性。本领域课题组高度集中，同组的两篇论文不构成独立印证。

### 5.3 撤稿与预印本状态

- 撤稿：查 Crossref `update-to` / OpenAlex 撤稿标记。**撤稿论文的论断直接拒入报告**，
  作为硬门禁，理由入审计。
- 预印本：不禁用（本领域大量有效工作首发于 arXiv），但在报告与表格中显式标注
  "未同行评审"，且**不能单独构成双源**。

## 6. 编排层：新工作流 `hsi_review`

节点链（系统综述 / PRISMA 式的工程化版本）：

```
检索策略生成（子问题 → 各库检索式 + 同义词扩展）
  → 多库并行检索 → 同 work 去重
  → 筛选（纳入/排除，逐篇记录排除理由）
  → 全文获取与分节 → 结构化抽取（数值 + 条件）
  → 冲突检测 → 表格透视 → 综合 → Critic
```

产出天然带"检索 N 篇、纳入 M 篇、排除理由分布"这种可复现叙述——而现有 run manifest
与检索快照（`reproducibility.py`）已经是它的底座，不用另建。

**新意图标签**（接现有三级级联）：`literature_review` / `method_comparison` /
`benchmark_survey` / `reproducibility_check` / `dataset_discovery`。按 `intent/types.py`
开头的设计原则，每个标签都必须落到下游动作上，落不到就不设。

## 7. 报告层：表格

### 表格由代码渲染，不由 LLM 自由生成

```
report_eligible findings → 按（行实体 × 列维度）透视成结构化表
  → 代码渲染 Markdown / CSV → LLM 只写表前后的叙述
```

- 每个单元格带 `[n]` 角标，指向 DOI 级引用；
- **空格子写"未报告"，禁止模型补齐**——自由生成的表最容易凭空长出数字；
- 数值格附协议脚注；**不同协议的数字不放进同一列**，或显式分列。

### 本领域要的四张表

1. **光学编码方案对照表**：方案 | 编码方式 | 色散元件 | 波段数 | 光谱范围 | 空间分辨率 | 需否标定 | 原型验证 | 引用
2. **重建算法对照表**：方法 | 类别（模型驱动 / 端到端深度 / 深度展开 / 生成先验） | 数据集 | PSNR | SSIM | SAM | 参数量 | FLOPs | 推理时间 | 协议 | 引用
3. **数据集与评测协议表**：数据集 | 波段数 | 光谱范围 | 场景数 | 仿真/真实 | 常用划分 | 引用
4. **证据强度表**：论断 | 独立 work 数 | 是否同组 | 预印本/已评审 | 冲突标记

### 输出通路

Markdown（现有 `.md` 导出直接可用）、CSV/XLSX（让人能拿去复核）。
**待查**：前端现有 Markdown 渲染是否支持 GFM 表格，未确认。

## 8. 分期

| 期 | 内容 | 单独交付的价值 |
|---|---|---|
| **1 骨架** ✅ | OpenAlex + arXiv 两个 `SearchTool`、`Source` 元数据扩展、DOI 级引用列表 | 风险最低；单独就把"出处"从 URL 提到 DOI |
| **R 报告层地基** ✅ | `ReportDocument` 结构化模型 + 内联 SVG 图表 + Markdown 投影 | 三格式输出与"带引用的表/图"的共同前提 |
| **R2 导出与打印** ✅ | 服务端装配 + `/document` 端点 + 打印样式表 + 可打印布局 + 预览 | HTML/MD/PDF 三格式落地，装置不再只存在于屏幕 |
| **3a 结构化数值** ✅ | `Quantity` / `ExperimentConditions` + 确定性数值校验 + 准入门禁 | 对照表的前提；数值抄错与错配单位可被程序逮住 |
| **4a 表格透视** ✅ | `Finding.entity` + `pivot_tables` + 口径分列 + 冲突并列 | 输出侧闭环：findings → 带逐格引用的对照表 → 图表 → 三格式 |
| **2 全文** | arXiv LaTeX 源解析 + OA PDF 解析 + 分节 + 节级筛选（改 Researcher context 组装） | 证据从摘要级进到 Results 级 |
| **3 证据加固** | `quantity`/`conditions` + 数值校验器 + 同 work 聚类修独立性 + 撤稿门禁 | 双源判定变成真的；数值不可比问题显式暴露 |
| **4 表格与图表填充** | 从已通过门禁的 findings 透视出 `TableBlock` + 四张领域表 + CSV 导出 | 用户明确要的交付物 |
| **5 工作流与评测** | `hsi_review` 模板 + 新意图标签 + 本领域评测集 | 用已知正确的 benchmark 数值量化抽取准确率 |

每期都要能单独跑通并带测试，不允许攒到最后一次合。

## 8.5 报告层地基（已完成）

三格式（HTML / MD / PDF）与"报告自行画图"的需求共同要求一件事：**报告先是结构，
再是文本**。改造前 `Report` 只有 `{query, markdown: str, citations}`，证据装置不在
报告里而是前端 join 出来的，所以 .md 导出装置全丢、PDF 无从谈起、带引用的表格一旦
被压进 LLM 的自由文本就再也取不回来。

新增 `deep_research/report/`：

| 文件 | 职责 |
|---|---|
| `document.py` | 块模型（Prose / Table / Chart）、证据记录、参考条目、概览、免责声明 |
| `charts.py` | 内联 SVG 渲染（bar / dot / grouped_bar / scatter / line）+ 主题与打印样式 |
| `markdown.py` | Markdown 投影（最保守子集） |

### 核心不变量：图表必有源表

`ChartBlock` **不携带任何数据**，只携带 `source_table`（某个 `TableBlock` 的 id）与取
哪几列。于是：

* 图上每个点都来自已通过证据门禁的单元格，都带 `[n]`；
* 模型永远碰不到图表数据——它只能在正文里写占位符说"这里放哪张图"；
* MD 渲染不了矢量图，但源表结构上必然存在，所以降级是**无损**的。

一张编造的柱状图比一句编造的话危险得多，因为它看起来是"数据"。把这条做成**结构
性质**而不是流程约定，判定器被绕过也不会失效——与"意图判定只能收紧"同一思路。

### 领域相关的取舍：柱状图零基线 vs 点图

柱长即数值，所以柱状图基线**必须**为零，截断 Y 轴是撒谎。但 CASSI benchmark 的
SOTA 差异常常是 35 dB 基座上的 0.5–2 dB，零基线会让所有方法看起来一样高。

正解不是截断柱状图，而是**换形式**：`dot` 用点的位置编码数值，位置没有"从零开始"
的语义，非零基线是诚实的。所以两种形式并存，按"要看绝对量还是看差异"选。

### 渲染实测发现的三个缺陷（已修）

按"画出来看一眼"的要求真渲染了一遍，暴露出三个光看代码发现不了的问题：

1. **量纲不同的多系列共用一根数值轴**。PSNR(31–38 dB) 与 SSIM(0.89–0.97) 并排时，
   SSIM 实测渲染成 **8px 残根**（PSNR 是 450px）——那个系列的信息被完全抹掉，而图
   看上去仍然人模人样。这是双 Y 轴撒谎的变体。现在 `value_columns` 单位不一致直接
   拒绝，并提示拆图或改用 scatter（散点两根轴是两个度量，本意如此，不误伤）。
2. **中文行标签按字符数裁剪会溢出**。24 个汉字在 12px 下约 288px，而标签区只有
   140px，文字会跑出 viewBox。改为按显示宽度裁剪（全角计 2、半角计 1）。
3. **MD 里同一张表出现两遍**。图降级成源表，而源表本身又独立成块。同一份数字出现
   两遍会被读成两组不同的数据。现在图只引用源表标题。

### 其他一并落地的决定

* **图表不引入依赖**。SVG 就是字符串拼接。刻意不用 matplotlib——它带 numpy +
  freetype，且默认无中文字体，中文标签会渲染成空白方块（与服务端 PDF 的字体坑同源）。
* **交互不靠 JS**。每个标记内嵌 `<title>`，浏览器原生 tooltip；自包含导出、打印
  预览、iframe 内嵌全都保留。"每个值都能在图外读到"由源表保证。
* **tooltip-only 信息提升为正式内容**。`verification_reason`、语义置信度百分比、
  `corroboration_reason`、完整内容哈希原先只活在 HTML `title=` 里——触屏不可达、
  打印不输出，也就是说这些信息在 HTML 移动端就已经在无声丢失。
* **缺失如实呈现**。未报告的单元格三种格式都写"未报告"，不补零不留空；图上标注
  "未报告"而不画成 0（画成 0 会让"未报告参数量"看起来像"零参数"）。
* **`Report.markdown` 与 `citations` 契约不变**。前端 `[n]` 跳转与快照覆盖率指标都
  依赖它，新模型是并列的增量产物，旧客户端与历史 run 不受影响。

### 尚未做（下一步）

打印样式表与应用内预览页。注意现状：`design-system.css` 里 `.report-view-body`
是滚动容器（`overflow: hidden` + 子元素 `overflow-y: auto`），**直接 Ctrl+P 只会
印出第一屏**；全项目目前只有一条打印样式（关掉入场动画）。另外浏览器打印不支持
`@page` margin box，自定义页眉页脚做不了，只能用浏览器自带的那套。

## 8.6 三格式导出与打印（已完成）

| 变更 | 文件 |
|---|---|
| 服务端规范化装配（原先只在前端 TS 里的 join） | `deep_research/report/assemble.py` |
| 结构化文档端点 | `GET /api/runs/{id}/document`（`api.py`） |
| 打印样式表 | `frontend/src/print.css` |
| 可打印报告组件 | `frontend/src/components/PrintableReport.tsx` |
| 导出/打印按钮 | `frontend/src/components/ReportActions.tsx` |
| 测试 | `tests/test_report_assemble.py`（17）、`tests/test_api.py`（+3）、`PrintableReport.test.tsx`（14） |

### 修掉了「Ctrl+P 只印第一屏」

`design-system.css:740-753` 的 `.report-view-body` 是滚动容器（`overflow: hidden`
+ 子元素 `overflow-y: auto`），流式期间还有 `height: min(68vh, 680px)`。滚动容器在
打印时只输出可视区域，所以之前直接打印会**丢掉折叠线以下的全部内容**。`print.css`
显式解掉所有相关容器的 `overflow` / `height` / `position`，这是它存在的首要原因。

### 侧栏是被「替换」而不是「隐藏」

屏幕上证据装置是**按需**的——点 `[n]` 开侧栏，一次只看一条。纸上没有「按需」，
所以 `display: none` 掉侧栏等于让整个装置消失。`PrintableReport` 无视「当前激活的
引用」，把全部证据铺成尾部附录，并另起一页（`break-before: page`）。

它**常驻 DOM**（屏幕上由 `.print-only` 隐藏），因此 Ctrl+P 与「打印」按钮走同一条
路径，不需要先跳转到某个打印路由。

### PDF：预览 + 打印，零依赖

浏览器打印对话框本身就是分页预览 + 打印机 + 「另存为 PDF」三合一，所以没有再造
预览器。另加一个应用内「打印预览」：按 A4 版心宽度（182mm = 210 − 14×2）就地呈现
打印布局，让用户在开对话框之前先看清附录长度与表格宽度。**它的分页是模拟的**——
真实分页由浏览器分页器决定，可能差一两行；它的价值是提前发现「附录 40 页」「表格
超宽」这类问题，不是像素级预演。

浏览器打印路线的三条固有限制已写进 `print.css` 顶部注释：

1. **自定义页眉页脚做不了**。Chrome/Edge 不支持 `@page` margin box，`position: fixed`
   也只在首页渲染。页码与日期只能用浏览器自带那套，且用户可关掉。
2. **页底脚注做不了**（`float: footnote` 无支持）。装置本来是正文的数倍体积，只能走
   尾注附录，所以影响不大。
3. **分页控制可用且够用**：`break-inside`/`break-after` 支持良好，`<thead>` 自动在
   每页重复——长 benchmark 表翻页后表头还在。

需要服务端批量生成 PDF 时再上 WeasyPrint，且做成**可选依赖**（缺它只少一个出口，
不是启动失败）。届时记得 `Dockerfile` 的 `python:3.11-slim` **不含 CJK 字体**，
不装 `fonts-noto-cjk` 中文会渲染成空白方块。

### 两处装配为什么允许并存

`assemble.py`（Python）与 `lib/evidence.ts`（TS）都做「引用号 → URL → findings →
徽章」这件事，但服务的是**不同出口**，不是同一输出的两份实现：

* Python 装配 → `.md` 导出、`/document` 端点、将来的服务端 PDF；
* TS 装配 → 交互视图与打印 DOM（复用页面里已有的数据，任何历史 run 立即可打印，
  无需额外请求，也没有加载竞态）。

两者由各自的测试钉在同一组口径上：引用顺序严格跟随 `citations`、正文尾部自动追加的
参考来源段落要剥掉（且只在结尾匹配）、拦截数缺失记为「不可用」而不是 0、完整哈希
不截断。让前端改为消费 `/document` 是后续可做的收敛项，不是当前的缺陷。

### 一并落地的口径

* **`[n]` 在纸上退回文本标记**。屏幕上它是按钮，保留按钮外观会让读者以为纸上能点。
* **正文链接把 URL 展开到括号里**（纸上点不了，不展开等于丢信息），但引用角标与
  附录里已显示全 URL 的地方不重复展开。
* **徽章不靠颜色**。打印常是灰度，所以用边框 + 文字承载状态。
* **强制明色**。深色主题在纸上是大面积墨 + 反白文字；`print.css` 与图表 CSS 各有
  一处同源的 `@media print` 覆盖。
* **`Report.markdown` / `citations` 与 `RunDetail` 一个字段都没改**，`/document` 是
  并列的新端点。

### Phase 1 实际落地清单

| 变更 | 文件 |
|---|---|
| 学术元数据模型 + Finding 携带渲染好的引用 | `deep_research/models.py`（`ScholarlyMetadata`、`Source.scholarly`、`EvidenceVerification.source_reference`） |
| 引用渲染单点实现 | `deep_research/citation.py` |
| OpenAlex 后端（DOI / 机构 / 撤稿 / OA 全文位置 / 倒排索引摘要还原） | `deep_research/tools/openalex.py` |
| arXiv 后端（Atom 解析、版本剥离、XML 失败关闭） | `deep_research/tools/arxiv_search.py` |
| 后端白名单与礼貌池邮箱 | `deep_research/config.py` |
| 后端组装（学术源无 key 分支） | `deep_research/execution.py` |
| 验证时刻盖上引用 | `deep_research/guardrails.py` |
| 参考来源列表升级，`citations` 契约不变 | `deep_research/agents/synthesizer.py` |
| 元数据与引用落库 | `deep_research/persistence/orm.py`、`sql_repository.py`、迁移 `0020` |
| 引用回退解析器适配新行格式 | `frontend/src/lib/evidence.ts`、`types.ts` |
| 测试（32 例） | `tests/test_scholarly_backends.py` |

Phase 1 收尾时的几处判断，写下来免得 Phase 2 重新纠结：

- **`peer_reviewed` 是三态。** OpenAlex 用 `primary_location.version` 推断而不是「有没有期刊名」；
  字段缺失落 `None`。把未知压成 `False` 会让报告给出自己并不掌握的结论。
- **arXiv 记录一律 `peer_reviewed=False`。** 取回的那份文档本身就是预印本，
  这与「是否另有已发表版本」是两件事；有 `journal_ref` 时把它填进 `venue`。
- **arXiv 的 `work_id` 剥掉版本号**（`arxiv:2205.10102`）。v1 与 v2 是同一份工作，
  Phase 3 判独立性时必须算成一个。
- **非学术来源的引用渲染为空串**，由调用方回退裸 URL。这样既有通用调研报告的
  参考来源段落逐字节不变——空串是「无学术引用可用」的信号，不是渲染失败。
- **文本折叠/还原必须发生在进 `Source.content` 之前。** 逐字证据校验匹配的对象就是
  `Source.content`，所以模型看到的、被哈希留证的、被校验的必须是同一份文本。
- **`Report.citations` 保持纯 URL 列表。** 前端按下标做 [n] 跳转、指标按 URL 与检索
  快照比对覆盖率，改成引用字符串会同时打断这两条链路。学术信息只进 Markdown。

## 8.7 结构化数值与确定性数值校验（已完成）

对照表不能从 `statement` 的自由文本里正则抠数字——那等于把编造风险从证据层搬到
报告层。所以先把数值脱离散文单独建模。

| 变更 | 文件 |
|---|---|
| 数值解析、单位归一化、容差、支持判定 | `deep_research/quantities.py` |
| `Quantity` / `ExperimentConditions` / `quantity_status` | `models.py` |
| 数值校验接入证据门禁 + 准入规则 | `guardrails.py` |
| Researcher 提示词要求填数值与条件 | `agents/researcher.py` |
| 落库 + 迁移 `0021` | `persistence/`、`alembic/versions/20260824_0021_*` |
| 数值与条件进证据附录 | `report/document.py`、`assemble.py`、`markdown.py` |
| 测试（30） | `tests/test_quantities.py` |

### 为什么逐字校验不够

逐字校验回答"这句话在不在原文里"。它对下面三种失败**完全无感**，因为被改的是
数值与单位的对应关系，不是措辞：

1. 引文抄对，但句子里的数字抄错一位（38.36 → 3.836）；
2. 引对一句同时提到 PSNR 与 SSIM 的话，然后把 SSIM 的 0.967 报成 PSNR；
3. 把"超过 35 dB"变成"等于 35 dB"——比较符被吞掉，下界变成点值。

所以数值校验是独立的一道确定性门禁：把模型声明的 `Quantity` 拿去和
`evidence_quote` 里真实出现的"数值+单位"比对，**纯代码、不调 LLM**。

### 容差按有效位数，恰好分开"取整"与"抄错"

声明 `38.36` 允许 ±0.005，`38.4` 允许 ±0.05，`38` 允许 ±0.5。于是原文 `38.36` 时：
声明 `38.4` **通过**（一位小数下的正确写法），声明 `38.3` **不通过**（应得 38.4）。
既不因为报告降低精度就误判编造，也不放过真的抄错。

> 实现时我先在模块 docstring 里写成"38.36 vs 38.4 判为不一致"，测试跑出来才发现
> 按有效位数给容差的行为比我写的那条更好，于是改的是文档而不是代码。

### 单位不符单独报出来

数值对上但单位不符是**最危险**的一类——数字是真的，含义是错的。它有独立原因码
`unit_mismatch` 并写明原文实际是什么，而不是笼统说"没找到"。

刻意**不做** `%` ↔ 无单位的换算：SSIM 有报 `0.948` 也有报 `94.8%` 的，自动换算会
把"单位不符"这个真实信号抹掉。宁可判不一致让人去看。

### 三态状态，向后兼容

`quantity_status` 是 `not_applicable` / `verified` / `unsupported`：

* **未声明数值** → `not_applicable`，**不影响准入**。绝大多数定性论断如此，既有行为
  逐条不变（有测试钉住）。
* **声明了却对不上** → `unsupported`，`report_eligible` 拒入报告。一张带假数字的
  对照表比一句假话危险得多，因为它看起来是"数据"。

注意此时 finding 的逐字状态仍是 `verified`——引文确实在原文里。两个门禁各自独立
判定，这样审计能看出"引文对但数字错"这种具体情形。

### 实验条件是可比性的前提

`ExperimentConditions`（数据集 / 划分 / 波段数 / 空间尺寸 / 协议 / 训练数据 / 硬件）
与数值同等重要：同一个 PSNR 在 28 波段与 31 波段、不同 mask 与训练集下并不可比。
抄了数字不抄条件，对照表越整齐越误导。原文没写就留空，不推测。

## 8.9 待办（按依赖顺序）

| 项 | 说明 |
|---|---|
| 同一 work 聚类 | 修 `guardrails.py` 按 registrable domain 判独立发布方导致的伪双源（arXiv + 期刊 + 机构库 = 3 个域，实为 1 篇） |
| 撤稿硬门禁 | 撤稿标记已在 `ScholarlyMetadata` 里，尚未接成准入门禁 |
| 全文解析 | arXiv LaTeX 源（表格是 `tabular` 结构化文本）+ OA PDF + 分节 + 节级筛选 |
| `hsi_review` 工作流 | 检索策略 → 多库检索 → 去重 → 纳入/排除 → 抽取 → 综合 |

## 8.10 表格透视（已完成）

`TableBlock` 的**生产者**。至此输出侧闭环：通过门禁的 findings → 对照表 → 图表 →
Markdown / HTML / 打印，全程 LLM 不碰表格数据。

| 变更 | 文件 |
|---|---|
| 透视器 | `deep_research/report/pivot.py` |
| `Finding.entity`（表的"行"） | `models.py`、`agents/researcher.py`、迁移 `0022` |
| 多引用单元格 + 列头脚注 | `report/document.py`（`TableCell.citations`、`TableColumn.note_ref`） |
| 自动接入装配 | `report/assemble.py`（`include_tables`，默认开） |
| 测试（19） | `tests/test_report_pivot.py` |

### 行是"对象"，不是"论文"

`Finding.entity` 是被比较的对象（方法名 / 光学方案名 / 数据集名）。**不能按论文聚合**：
一篇 CASSI 论文常同时报告自己与多个 baseline 的数字，按论文分行会把它们压进一格。

### 最关键的一条：口径不同的数值分列

按 `(指标, 实验条件签名)` 分列。同一个 PSNR 在 28 波段与 31 波段下不可比，同列摆放
越整齐越误导——读者会直接横向比较。分列后列头各自指向口径脚注。

单一口径时不加脚注，避免给单口径表凭空添噪声。

**"未标注口径"自成一列**，不与任何已知口径合并："不知道条件"不等于"条件相同"，
静默合并等于替读者做一个我们没有依据的判断。

### 冲突并列，但排除出图表

同一 `(对象, 指标, 口径)` 出现不同数值时，表里并列呈现 + 标注 `⚠`，不静默挑一个。
但该格 `numeric` 置空，**排除出图表**——把有争议的值画成一个点等于替读者裁决，
而图上看不出那里有分歧。表里能看到、图上不出现，这个不对称是刻意的。

按显示写法去重，所以 `38.36` 与 `38.360` 不算分歧。

### 进表的四个必要条件

通过报告准入（含数值校验）、有出处角标、有 `entity`、有可用 `Quantity`。缺任一项时
该 finding 仍出现在正文与证据附录里，只是无法定位到表格的某一格——不是被丢弃。

数据不足以成表（<2 行或 <2 个已报告格）时返回空列表，不产出退化的 1×1 表。

### 向后兼容

历史 findings 没有 `entity` / `Quantity`，透视器返回空列表，既有报告产物逐字节不变，
所以自动开启是安全的（有测试钉住）。

### 实测（SQLite 端到端）

4 条 findings（3 条 28 波段、1 条 31 波段）→ 自动分成 `psnr__1` / `psnr__2` 两列，
各带脚注；CST-L 在 28 波段列显示"未报告"；`dot` 图在该列只画 3 个点而不是把缺失
画成 0。

## 9. 明确不承诺的

- **不保证论断为真**。保证的是出处可追溯、引用可逐字核验、单源/双源/冲突/撤稿可判定。
- **付费全文拿不到**（IEEE、SPIE 部分）。这类只能停在元数据 + 摘要级，报告里必须显式
  标注证据等级，**不能和全文级证据不加区分地混进同一张表**。
- **表格抽取有失败率**。抽不到就是"未报告"，不猜。
