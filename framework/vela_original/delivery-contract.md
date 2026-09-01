# 交付契约(所有产出交付物的 step 必读)

仓库根 `AGENTS.md`「交付」一节的完整版。样式起点、四件套的具体规则、`output/` 卫生、
Markdown 与 HTML 的硬规则、以及交付前必须跑的验收命令都在这里。

## 样式起点(先定这个,再谈内容)

排版不是自由发挥。每类产物都有规定的基底,**从基底改内容**,不许自己另起一套:

| 产物 | 起点 | 命令(从仓库根跑,路径都是全的) |
|---|---|---|
| 报告类 `.docx` | Apevon 基底 | `python3 .claude/skills/docx/scripts/python/create_apevon_report_base.py <out>.docx` |
| 报告类 `.pdf` | **定稿 DOCX** | `soffice --headless --convert-to pdf <out>.docx` |
| 论文/综述 `.pdf` | LaTeX 模板 | `paper-writer` / `literature-survey` 的 `templates/*/compile.sh`(xelatex) |
| `.pptx` | 八套模板选一套 | `.claude/skills/pptx/styles/_index.md` 选型,`deckkit.Deck("<style>")` 构建 |

- **报告类 PDF 必须由定稿 DOCX 转**,样式随之继承;从 Markdown 直转 PDF 会绕过整套基底。

- **禁止分散对齐**:`w:jc="distribute"` 会把一行铺满版心, 短标题被拉成「环　　境　　变　　量」, 转出的 PDF 一样。标题居中或左对齐, 正文两端对齐(`both`)。`style_gate.py` 会拦。
- **禁止 `pandoc --print-default-data-file reference.docx`** 及任何"拿 pandoc 默认模板改改"的写法
  —— 那是 pandoc 的样式,不是本仓的。实测事故:任务 `d1b8ffb0` 四件套全绿通过,交出去的是 pandoc 默认排版。
- **`env_check.sh` 报 `NOT READY` 不等于不能干活**:走"拷基底再改内容"这条路只需要 python3 + LibreOffice,
  docx-js 缺席可以继续(详见 `docx/SKILL.md`「实测校正」)。
- 样式出处有机械门,见下方验收命令块最后一条。

## 默认文档交付套件

当用户要的主要最终产物是报告、分析、方案、指南、备忘录等**可阅读文档**,且用户没有指定格式时,默认交付同一主文档的四种版本:

- `<name>.md`:内容真源,便于继续编辑;
- `<name>.docx`:专业排版的可编辑版;
- `<name>.pdf`:与 DOCX 内容、层级和图表一致的定稿版;
- `<name>.html`:专业排版、可独立打开的自包含阅读版。

这是默认交付契约,不属于“即兴造副产物”。具体规则:

1. **用户格式要求优先**:用户明确只要某种格式时,只交付该格式;用户明确要多种格式时,按其清单交付。用户只提内容、step 内部先用 `.md` 起草,不等于用户明确“只要 Markdown”。
2. **只包装主文档**:中间笔记、证据索引、代码、数据、日志、图片不机械转成四份。PPT、表格、思维导图及其它专用格式按原契约交付。论文/综述已以 LaTeX/PDF 为强制主产物时,不再为每个 Markdown 辅助文件重复转换。
3. **同源生成**:先定稿 Markdown,再由它生成 DOCX 和自包含 HTML;PDF 优先由最终 DOCX 转换,避免四份内容漂移。不得独立重写四遍。
4. **排版与验收**:DOCX 从上方「样式起点」的 Apevon 基底起,PDF 由定稿 DOCX 转;生成前必须读 `.claude/skills/docx/SKILL.md`、`.claude/skills/pdf/SKILL.md`。DOCX/PDF 必须渲染成逐页图片并检查全部页面,确认无裁切、重叠、乱码、破表格和图片缺失。只有命令成功不算验收通过。HTML 还必须满足下方“交付 HTML 的硬规则”。
5. **该配图就配图**。报告、分析、方案、调研这类文档不是天然纯文字:说明"这套东西怎么运转"的地方给一张示意图(image-2),有数据支撑趋势/对比的地方给一张图表(代码),按仓库根 `AGENTS.md`「图的两条路径」选。不要因为"这不是论文"就默认交纯文字,也不要为凑数硬塞。
6. **交付物必须带上本任务已经画出来的图**。前面的 step 生成了 `figures/*.png|pdf`,终稿却一张不放,是最常见的交付事故:实测两次交付里 `final-report.md/html/docx/pdf` 四份**全部零图**,而工作区里躺着 4 张画好的图;其中一份正文还写着"HTML 版已用 base64 内嵌图表",实际一个 `<img>` 都没有。**不确定就去数,别凭印象写。**
7. **报告和论文禁用直角引号**:`「`、`」` 不得出现在标题、正文、题注、表格、目录、参考文献、页眉页脚或任一交付格式中。中文普通引文用 `“”`,嵌套引文用 `‘’`,书刊标题用 `《》`;不要以“忠实引用”为由保留直角引号。
8. **验收靠命令,不靠"我看过了"**。逐页看图是必要的、但不充分——上面两起事故都渲染了逐页 PNG、都"检查"过、都放行了。交付前对每份产物跑下面这段,任何一行 `FAIL` 都不许交:

   ```bash
   # 图有没有真的进去(数字是 0 就是没进去)
   python3 -c 'import zipfile,sys; z=zipfile.ZipFile(sys.argv[1]); \
     n=len([x for x in z.namelist() if x.startswith("word/media/")]); \
     print(("OK " if n else "FAIL ")+f"docx embedded images={n}")' out.docx
   pdfimages -list out.pdf | tail -n +3 | wc -l      # 0 = PDF 里没有位图
   grep -c '<img' out.html                            # 0 = HTML 里没有图
   grep -c 'src="data:image' out.html                 # 必须等于上一行(否则是相对路径,平台里必裂)

   # 样式出处(上面那几行只查内容,查不出"用了谁的排版")
   python3 .claude/skills/_shared/scripts/style_gate.py out.docx out.pdf   # 论文/综述加 --kind paper
   python3 .claude/skills/_shared/scripts/style_gate.py deck.pptx          # 幻灯片

   # Markdown 可渲染性(裸 HTML / 页内锚点 / wikilink)
   python3 .claude/skills/_shared/scripts/markdown_gate.py out.md
   ```

   `style_gate.py` 只回答一个问题:**这份产物是不是从规定的基底起的**。docx 查 Apevon 基底的
   专有样式并拦 pandoc 默认模板指纹;pdf 查 `/Producer`(报告类须为 LibreOffice、论文类须为 TeX);
   pptx 查每页背景是否都落在 `styles.json` 里同一套模板。退出码非 0 不许交。

   含中文的 PDF 另跑 `python3 .claude/skills/paper-writer/scripts/figqa.py out.pdf`——它会检出文字重叠、逐字换行和**乱码方框**。乱码在文字层里是查不出来的(码位还在,只是没字形),`pdftotext` 和肉眼扫图都会放过它,只能靠这个脚本。

## 交付卫生(所有 step 必读)

`output/` 里只放**正确、自包含、契约要求的**交付物。除此以外一律不进 `output/`:

- **不确定要不要交、还是半成品、还是转换用的中间态 → 放 `work/`**,别泄漏到交付树。宁可 `output/` 干净少几个文件,也不要塞一个打不开/引用断裂的破文件——用户点开的就是它,坏文件比没有更糟。
- **不要即兴造契约外的副产物**。cap SKILL / references 让你交什么就交什么;想额外做个"预览版/中文版/转换版"之前,先确认它能在平台里正确打开——做不到就别放进 `output/`。(历史
…[truncated]