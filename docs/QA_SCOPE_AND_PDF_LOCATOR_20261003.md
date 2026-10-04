# 问答来源选项与论文定位核查（2026-10-03）

## 本地已完成：问答来源选项

- `QaPage.tsx` 使用独立的来源选项布局，带来源图标、未选中的加号和选中的勾号。
- `pages/qa.css` 提供圆角选项、选中底色、键盘焦点样式、独立提示行及知识库项目选择行，支持窄屏换行。
- 保留原生 checkbox 的键盘与读屏语义，检索开关、项目选择和提交规则不变。
- 浏览器预览发现 `QaAnswerBody.tsx` 的 React 导入放在文件末尾，开发服务转换后触发 `Cannot access 'createContext' before initialization`；已将该导入移至文件顶部，页面恢复加载。
- 现有 `QaPage` 7 项和 `QaAnswerBody` 9 项测试通过；TypeScript、相关文件 ESLint 与 `git diff --check` 通过。
- 使用本地 Vite 与模拟 API 数据检查桌面、390px 窄屏、键盘 Space 切换、两个来源勾选、项目选择及无横向溢出。截图位于 `artifacts/qa-scope-{default,selected,mobile,desktop}.png`。
- 临时预览浏览器与开发服务已关闭；本节记录本地检查，不作为部署状态证明。

## 当前论文分块依据（代码已核实）

1. PDF 文本提取：`deep_research/tools/oa_pdf_fulltext.py` 使用 PyMuPDF `page.get_text("dict")`，遍历文本块、行和 span，生成页面文字。没有在这一层做语义句子划分，也没有额外重排双栏阅读顺序。
2. 章节识别：`_sections_from_pages()` 使用标题与编号规则识别章节，保留章节涉及的页码范围。
3. 导入分块：`deep_research/library/ingestion.py` 对各章节调用 `_chunks_for_text()`，每块最大约 3,600 字符，相邻块重叠 320 字符。
4. 边界：长度窗口后半段寻找最后一个双换行、中文句号或英文句号加空格；找不到则按长度截断。这是章节与长度规则结合标点启发式，并非逐句的语义分块。重叠区的起点也不保证处于句首。
5. 定位元数据：片段带章节、页码范围与字符位置；页码范围来自所在章节，不能直接当作引文的精确页码。

## 当前 PDF 引文定位（源码已核实）

- `5742d20` 已移除引文开头、结尾片段的回退匹配。`frontend/src/lib/pdfMatch.ts` 要求标准化后的完整引文唯一命中；缺失或重复引文返回未定位，不取第一次出现的位置。
- 匹配保留 PDF.js 文本项中的 UTF-16 起止偏移，支持跨文本项、跨页完整引文，并处理大小写、空白、全角字符、连字、组合重音、断行连字符及科学计数法的部分排版差异。页面索引在匹配结果内部从 0 开始，界面显示和 PDF.js 取页从 1 开始。
- `frontend/src/lib/pdfHighlight.ts` 使用 PDF.js `TextLayer` 与浏览器 `Range.getClientRects()` 测量命中字符，按页面旋转映射到画布坐标。高亮由命中的字符范围决定，不再涂满整个文本项；DOM 无法进一步分割的连字以其实际字形范围显示。
- `PdfViewer.tsx` 先清除旧高亮，再检索当前文档，显示完整匹配、未唯一命中、提取失败或超时状态；切换引文会取消旧定位，避免迟到结果覆盖新选择。跨页引文高亮各命中页面，并将引文开头居中。
- 本次补充修复：定位扫描同时读取各页真实尺寸，React 提交页面布局后再计算滚动距离，避免远页仍按第一页尺寸占位导致偏移。`ReaderPage.tsx` 的重试标记改为状态递增，连续点击不依赖 `Date.now()` 是否变化。
- `ReaderPage.tsx` 仍按证据来源选择文档，再传递 `evidence_quote` 和重试标记。章节页码范围与分块字符位置未作为 PDF 精确坐标使用；它们与 PDF.js 文本项的偏移不是同一坐标体系。

## 本批验证证据

1. 回归复现：新增两个测试在修复前失败，分别观察到混合尺寸页仍为 `800/800/800` 的占位高度，以及固定时间戳下两次点击产生相同 token。修复后，对应页面高度为 `800/1200/400`，定位在布局更新后滚动，重复点击产生不同 token。
2. 定向命令：`npm test -- src/lib/pdfMatch.test.ts src/components/PdfViewer.test.tsx src/pages/ReaderPage.test.tsx`，3 个文件、37 项测试通过；TypeScript、四个改动文件的 ESLint 与 `git diff --check` 通过。测试覆盖完整引文边界、重复命中拒绝、字符偏移、跨页匹配、失败清理、超时与异步竞态，以及上述新增回归。
3. 真实浏览器：本地 Vite 的 `frontend/pdf-locator-preview.html` 通过模拟 API 读取 `artifacts/reported-multispectral-original.pdf`。以下两条完整引文均显示“已定位完整引文 · 第 4 页”，各形成 3 个字符范围矩形；引文开头与滚动视口中线偏差小于 3 px：
   - “However, there are no multispectral stereo databases for training disparity estimation networks available.”
   - “Therefore, we proposed a novel data augmentation method to generate pseudo spectral images, which is described in Section IV.”
4. 真实 PDF.js 坐标验证：在 Chromium 中生成包含 `Before. Target quote. After.` 的 Courier PDF，只匹配 `Target quote.`。验证 0°、90°、180°、270° 与 `UserUnit` 1、2 的 8 种组合；矩形均处于对应页边界内，沿文字方向的长度分别约为 156 px、312 px，与 13 个 20 pt Courier 字符的预期宽度一致。
5. 未命中引文清除旧高亮，所有完成的浏览器定位均无 `.pdf-text-measure` 临时层残留。本地复核脚本为 `artifacts/pdf-review-browser.js`；这些 `artifacts` 文件是本机验证材料，不是仓库内置测试资源。本次启动的 Vite 服务与浏览器会话均已关闭。
6. 本轮集成检查：确认没有其他前端测试或构建任务后，在包含本批 PDF 修复及 `SettingsPage`、`settingsHelp` 期限帮助改动的工作树运行 `npm test`、`npm run lint`、`npm run build`，全部退出码为 0。全量测试为 58 个文件、421 项通过；构建包含 TypeScript 检查、Vite 打包及 PDF.js 字体/字符映射资源复制。

## 尚未验证的边界

- 扫描件或缺失文字层的 PDF 未做本批实测；本实现没有新增 OCR，不能承诺这类文件可定位。
- 超长 PDF 的全文提取耗时、缓存内存与定位超时阈值未做压力验证；本次真实论文为 12 页，不能据此证明长文档性能。
- 多文档切换、来源映射歧义及复杂跨文档引用尚未完成真实浏览器端到端验证。当前测试证明了指定文档中的定位行为，不证明所有来源映射都准确。
- 混合页面尺寸的远页定位已有组件回归，但尚未用真实混合尺寸长 PDF 验证完整滚动流程；旋转和 `UserUnit` 的真实浏览器验证属于独立坐标测量。
- 任意双栏顺序、页眉页脚插入、公式或 PDF.js 与后端提取顺序不一致仍可能导致完整引文无法匹配。当前会报告无法准确定位，没有用片段命中替代完整证据。
- 本批已在本地原论文验证上述两条引文落于第 4 页，但没有重放此前截图对应的完整交互记录，不能认定“跳到第 10 页”的唯一根因，也不能把本地结果视为已发布环境的验收。
