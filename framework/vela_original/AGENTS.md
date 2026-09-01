# AGENTS.md

Apevon Science「AI 研究员」功能仓库。Vela 的 `repo_url` 指向这里,运行时在容器中 `git clone --depth=1`。

## 运行环境:无人值守(headless)

- **绝不停下来等确认**:step prompt 就是已批准工单;需要决策时自行决断并在产物中写明理由。
- 研究不完整但已有结果置 `partial` 并说明缺口;只有明确不该执行才置 `skipped`;认证/存储/调度错误交给 worker 判定,不要伪装成 `failed`。
- **技能不按 description 自动触发**:planner 必须在 step prompt 显式写「读 `.claude/skills/<name>/SKILL.md`」。

## 结构与路由

`.claude/skills/` 是唯一技能层,每个技能自带 `references/`、`templates/`、`scripts/`:

- 研究链路:`ai4s-agent`(元编排)、`research-explorer`(选题)、`literature-survey`(综述)、`experiment-suite`(实验)、`paper-writer`(论文)。
- 通用手段:`academic-search-v2`(学术检索/深读)、`image-gen`(出图)、`docx`/`pdf`/`pptx`/`xlsx`(文档)。
- 入口读 `.claude/skills/ai4s-agent/SKILL.md`;planner 协议读其 `references/plan-json-protocol.md`;跨技能详规在 `.claude/skills/_shared/references/`。
- 链路不强制全跑:仅学术科研任务走完整或部分研究链;「调研后做 PPT」只取 `research-explorer` + `pptx`。

## 目录约定(所有 step 必读)

一个任务一个 slug,两棵树同名同结构,严禁混用:

| 目录 | 内容 | 权限 |
|---|---|---|
| `work/<slug>/<stage>/` | 代码、LaTeX、`.bib`、脚本、数据、日志、提示词、中间态 | 可写 |
| `output/<slug>/<stage>/` | PDF、PNG、DOCX、HTML、model、`*-sources.zip` 等可直接打开的成品 | 可写 |
| `.claude/` | 技能本体 | 任务执行时只读;仅本仓维护工单可改 |
| `.vela/` | 编排状态(`plan.json`) | 禁放产物/中间文件 |

- `<stage>` 为 `explore`/`survey`/`experiment`/`paper`;轻任务可省略。
- 在 `work/` 构建,完成后复制到 `output/`;源码需交付时打成 `*-sources.zip`;半成品留在 `work/`。
- 禁用 `/tmp/`;planner 必须给每个 step 写明具体输出路径。

## 资料检索

- 默认免费路线是 `WebSearch → WebFetch → 立即写 bibliography.bib`;概念、文档、博客、GitHub、报错、行情不得走付费学术检索。
- 仅在需要真实被引数、DOI/arXiv ID、作者发表列表或论文正文细节时,显式路由并读取 `.claude/skills/academic-search-v2/SKILL.md`。
- `deepread.sh` 不占检索额度,但必须显式给第二参数 `work/<slug>/<stage>/deepread/<md5>/`;对 `result.md` 先 `grep -n` 再局部读取。
- Scholar API 遇到 `429` 或缺少凭据时降级回 WebSearch/WebFetch,记录降级但不停工、不降低引用目标。细则读研究技能的 bibliography reference。

## 图的两条路径

| 图的性质 | 路径 |
|---|---|
| 数据算出的柱状/折线/散点/热力/时间线/覆盖矩阵 | matplotlib/seaborn;读 experiment/survey figure playbook |
| 架构/框架/方法/分类法/机制/概念图 | image-2;读 `.claude/skills/image-gen/SKILL.md` |

- image-2 每张图一个提示词、一次调用;错误时改提示词重跑,不刷候选;生成后必须打开检查语义、文字、裁切和编造内容。
- **图中可见文字必须匹配正文主语言**;语言声明、调用参数、外文摘要/参考文献处理及失败降级规则全部以 `image-gen`、`paper-writer`、`literature-survey` 技能为准,禁止中文图静默降级为英文。
- matplotlib 禁画方框箭头图;需要真矢量、精确字体或图中公式才用 TikZ;有稳定论文 PDF 时主框架图走 `generate_paper_framework_once.py`。
- PPT/Word 图片分别读 `.claude/skills/pptx/images.md`、`.claude/skills/docx/images.md`。

## 交付

**每类产物都有规定的样式起点,不许自己另起一套**(排版不是自由发挥,是从基底改内容):

| 产物 | 样式起点 |
|---|---|
| 报告/分析/方案/指南/备忘 → `.docx` | `docx/scripts/python/create_apevon_report_base.py` 拷 Apevon 基底 |
| 同上 → `.pdf` | 由**定稿 DOCX** 转(样式随之继承),不从 Markdown 直转 |
| 论文/综述 → `.pdf` | `paper-writer` / `literature-survey` 的 LaTeX 模板,xelatex 编译 |
| 幻灯片 → `.pptx` | `pptx/styles/` 八套选一套,全程一致;改用户 deck 时沿用原模板 |

- 用户要可阅读文档但未指定格式时,同源交 `.md`/`.docx`/`.pdf`/`.html` 四份。
- 该配图就配图,且本任务生成的图必须进入终稿;验收靠命令和逐页看图,不靠口头确认。
- **交付 `.md` 里不许出现裸 HTML 标签和页内锚点**(`<a id=…>`、`<br>`、`[[N]](#ref-N)`、`](#标题)`)
  —— 平台的 md 渲染器会把标签转义成字面文字,fragment 则落到宿主页 URL 上,点了跳空。
  正文引用写纯文本 `[N]`,链接只用绝对 http(s) URL。
- **样式出处是机械门**:`python3 .claude/skills/_shared/scripts/style_gate.py <产物…>` 必须退出 0;
  交付 `.md` 另跑 `markdown_gate.py`(同样退出 0);
  `pptx` 另跑 `fitcheck.py`(error 必须为 0)并用 LibreOffice 或 `pptx_preview.py` 看图。
- 四件套、`output/` 卫生、HTML 自包含和完整验收命令读 `.claude/skills/_shared/references/delivery-contract.md`。

## 长任务

单 step 每窗口 4 小时、最多 3 个窗口,整个任务 48 小时;进度必须增量落盘,不要杀掉正常进程,也不要缩小模型/epoch 迁就窗口。续跑、GPU 切分和 Subagent 规则读 `.claude/skills/_shared/references/long-task-windows.md`。

## 开发(仅本仓维护时)

直接修改 `.claude/skills/`,commit + push 后由下一个 Vela 任务 clone 新版。提交信息禁止 AI 署名、`Co-Authored-By`、`Generated with` 等宣传 trailer。