# N7 思维导图阅读与分支交付

本次在 `improvement/scenario-quality-20261006` 本地实现，未部署、未调用付费模型。
工程样例是明确标记的合成数据，不能作为新研究内容已经通过人工评价的证明。

## 实际链路

| 项目 | 实现与行为 |
| --- | --- |
| N7.1 短标签与完整说明 | `MindmapNode.details` 保存完整说明、数字和适用条件；label 的 32 字符只是软预算。writer、核验单元、局部修订、重复候选与 Markdown 均消费 label+details；数字藏在详情中仍会被核验。新大纲以 `[N0.1]` 标示节点路径，不当作文献编号。 |
| N7.2 总览与分支 PNG | 总览明确只含一级分支。分支采用父节点与直接子节点图块，随后交付同一节点的完整说明、公式和引用，自动按 A4 分页；不把整幅大图压成不可读缩略图。PNG 保留内容版本、分支、页数、节点路径与跨分支索引元数据。 |
| N7.3 关系与重复检查 | 复用七类关系和既有保守重复候选规则；HTML 展示关系数量、审核状态、候选节点路径，保留两端证据。不同条件、数据划分、数值或引用的节点不因短标签相同自动合并。程序不删除候选节点。 |
| N7.4 多格式一致 | HTML 完整大纲、节点说明面板、PNG、Markdown 与 `-mindmap-index.json` 来自同一冻结图。索引保留原引用与展示引用的映射、原 evidence_ids 和审核输入 hash。分支图中的书目编号沿用已有 bibliography/display_citations，不混用片段序号。 |

新 writer 输出 schema_version=2。MINDMAP_POLICY_VERSION 更新为 4，已被既有交付/写作指纹引用。
旧 schema 1、空 details 保持原有大纲和审核哈希口径；新增详情、修改条件或事实会改变审核输入，不能借用旧决定。
新渲染不覆盖旧交付版本。正文或节点审核失败时仍按原门阻止正式图形/索引交付，没有因为新功能放宽质量门。

所有新文件由既有 `render_bundle → delivery_store → /deliverables/{name}?version=...` 发布与下载：

- `*-mindmap.html`：离线可用的完整导图，键盘查看详情、折叠分支、定位引用。
- `*-mindmap.png`、必要时 `*-mindmap-overview-NNN.png`：一级分支总览，明确不是完整内容。
- `*-mindmap-bNNN-pNNN.png`：指定分支的图块与说明页，按页交付。
- `*-mindmap-index.json`：全部节点、分支归属、图块覆盖、关系方向、引用与重复候选。
- 原 Markdown：保留全部节点的完整标签、详情、条件、公式和引用。

图片使用现有本地 Matplotlib、PyMuPDF 与数学排版链，没有增加外部转图服务。
每页最多 250 万像素、整包该组 PNG 最多 80 页；超限会明确记录不可自动修复的图片失败，
保留完整 HTML/Markdown，并不会声称已交付缺失页面。

## 验证证据

`test_mindmap_readable_delivery.py` 的 10 项新增测试覆盖详情核验、旧版审核兼容、条件/引用差异、
重复候选保留、节点及图块全覆盖、展示引用版本化、HTML/公式/转义、PNG 分页/上限、
writer 到冻结文件再经真实 API 下载及 SHA-256 核对。
合并既有关系、证据、局部修订、去重和数学交付测试，共 **84 项通过**。
日志：[文本](validation/n7-backend-20261006.txt)、[JUnit XML](validation/n7-backend-20261006.xml)。

受控 28 节点样例生成 **11 张 PNG**，本地单个 Python 进程约 **3.38 秒**、峰值 RSS **271 MiB**。
这不是生产 1 GiB 容器的并发容量测量。数据见
[渲染结果](validation/n7-readability/render-results.json)。

真实 Chromium 页面在 1366×768、390×844、1280×600 暗色窗口验证：
没有页面横向溢出，完整大纲保留 28 个节点；键盘折叠/展开有效；节点详情中的公式实际显示，
点击引用能打开原文所在的折叠区域。结果见
[浏览器记录](validation/n7-readability/browser-results.json)。

截图：[桌面](validation/n7-readability/desktop.png)、[手机](validation/n7-readability/mobile.png)、
[低高度暗色](validation/n7-readability/low-height-dark.png)。实际分支图与说明页位于同目录的
`sample-mindmap-b001-p001.png` 等文件，最新有效文件清单以 render-results.json 为准。

可复现命令：先运行 `python scripts/check_mindmap_readability.py`，再用本地静态服务打开输出的
mindmap.html；`scripts/check_mindmap_browser.js` 可由 playwright-cli 执行固定的交互与截图检查。

## 仍需人工或环境验证

用户需在实际新导图上检查标签是否合适、展开后含义是否完整、关系组织是否有帮助，以及常用屏幕/A4 阅读体验。
本次没有测量真实模型生成质量，也没有在生产 1 GiB 容器中验证并发内存。
长公式和极端大图仍可能达到既有格式/期限限制；失败时应读取完整 HTML/大纲并查看明确的交付错误，
不能把缺少图片页解释为该分支不存在。
