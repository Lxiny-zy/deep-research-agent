/**
 * 研究问题 → 页面标题：去掉误带的 Markdown 标题符号，并只取第一行。
 *
 * 数据分析的输入是「问题 + 粘贴的 CSV」，同行评审的输入可能是链接加一段说明；
 * 把整段原文当标题会把表格塞进页眉。标题最多 80 字，超出以省略号收尾。
 */
export function displayReportTitle(value: string) {
  const firstLine =
    value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find(Boolean) ?? ''
  const title = firstLine.replace(/^\s*#{1,6}\s+/, '').trim()
  if (!title) return '研究报告'
  return title.length > 80 ? title.slice(0, 80) + '…' : title
}
