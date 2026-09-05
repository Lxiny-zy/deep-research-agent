/** Remove a Markdown heading marker accidentally included in a research query. */
export function displayReportTitle(value: string) {
  return value.replace(/^\s*#{1,6}\s+/, '').trim() || '研究报告'
}
