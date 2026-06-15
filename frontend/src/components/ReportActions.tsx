import { useState } from 'react'
import { downloadText, slugify } from '../lib/download'

// 报告操作：复制到剪贴板 + 下载 .md。markdown 为空时禁用。
export default function ReportActions({
  markdown,
  query,
  runId,
}: {
  markdown: string
  query: string
  runId?: string
}) {
  const [copied, setCopied] = useState(false)
  const disabled = !markdown

  async function copy() {
    try {
      await navigator.clipboard.writeText(markdown)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  function download() {
    const short = runId ? `-${runId.slice(0, 8)}` : ''
    downloadText(`${slugify(query)}${short}.md`, markdown)
  }

  return (
    <div className="report-actions">
      <button type="button" className="btn ghost sm" onClick={copy} disabled={disabled}>
        {copied ? '✓ 已复制' : '复制'}
      </button>
      <button type="button" className="btn ghost sm" onClick={download} disabled={disabled}>
        下载 .md
      </button>
    </div>
  )
}
