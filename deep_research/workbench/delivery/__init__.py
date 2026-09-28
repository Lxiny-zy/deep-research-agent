"""同源多格式交付。

定稿 Markdown 是唯一内容真源；DOCX、PDF、HTML 都由同一份 Markdown 派生：

* ``html``  —— Markdown → 语义 HTML（图片 base64 内联、CSS 全内联，单文件自包含）；
* ``pdf``   —— 由上面的 HTML 经 PyMuPDF 排版为 A4 PDF（无需 LibreOffice / TeX）；
* ``docx``  —— Markdown 语法树直接映射为 python-docx 段落、列表、表格、图片；
* ``pptx``  —— 结构化幻灯片（SlideDeck）渲染为带演讲备注的演示文稿；
* ``mindmap`` —— 层级大纲渲染为可交互 HTML 与静态 PNG。

各格式共用 ``markdown.parse_blocks`` 的同一棵块树，所以「DOCX 里多一段、PDF 里少
一段」这类同源漂移在结构上不可能发生；验收门再用跨格式计数把它钉死。
"""
