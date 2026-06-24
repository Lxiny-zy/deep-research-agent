import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function ReportView({
  markdown,
  streaming,
}: {
  markdown: string
  streaming: boolean
}) {
  if (!markdown) {
    return (
      <p className="muted small">
        {streaming ? '报告生成中…' : '报告将在这里显示（含 [n] 引用与参考来源）。'}
      </p>
    )
  }
  return (
    <div className="report markdown-content">
      <Markdown remarkPlugins={[remarkGfm]}>{markdown}</Markdown>
      {streaming && <span className="cursor">▍</span>}
    </div>
  )
}
