import { useEffect, useState } from 'react'
import { downloadRunDocument, type RunDocumentFormat } from '../api/client'
import { downloadBlob, downloadText, slugify } from '../lib/download'
import { AppIcon } from './AppIcon'

// 报告操作：复制 / 下载 .md / 打印预览 / 打印·存为 PDF，以及服务端结构化导出。
//
// 「下载 .md」优先走服务端 /document.md：那份 Markdown 带着证据装置（逐字引文、
// 验证状态、快照哈希），而浏览器里的 markdown 只是综合者写的正文。拿不到 runId
// （或服务端不可用）时回退到本地正文——退化的是完整性，不是可用性。
//
// 「打印·存为 PDF」直接调 window.print()：浏览器的打印对话框本身就是分页预览 +
// 打印机 + 「另存为 PDF」三合一，不需要我们再造一个预览器。零依赖，桌面版打包
// 也不受影响。它与「下载 PDF」不同：后者是服务端渲染的矢量图版本（需 pdf extra）。
//
// 「打印预览」是应用内的一层：按 A4 版心宽度就地呈现打印布局，让用户在打开对话框
// 之前先看清附录长度与表格宽度。它不呈现分页——真实分页由浏览器决定。
export default function ReportActions({
  markdown,
  query,
  runId,
  includeHsiTables = false,
  tableOptions = [],
  documentReady = false,
  previewing = false,
  onTogglePreview,
}: {
  markdown: string
  query: string
  runId?: string
  includeHsiTables?: boolean
  tableOptions?: { id: string; label: string }[]
  /** 结构化文档是否已就绪。未就绪时服务端导出只会产出空文件，不如不给点。 */
  documentReady?: boolean
  previewing?: boolean
  onTogglePreview?: () => void
}) {
  const [copied, setCopied] = useState(false)
  const [selectedTableId, setSelectedTableId] = useState('')
  const [exporting, setExporting] = useState<RunDocumentFormat | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const disabled = !markdown
  // 导出依赖服务端已装配好的文档。只判 runId 的话，运行仍在流式阶段时按钮就是
  // 可点的，而那时导出的 CSV 是空表、PDF 是空正文——用户拿到一个"成功"的空文件，
  // 比按钮暂时不可点更难理解。
  const exportDisabled = !runId || !documentReady || exporting !== null
  // 单表导出还需要真的有表。非 HSI 运行通常一张表都没有，此时 CSV/XLSX 无意义。
  const tableExportDisabled = exportDisabled || tableOptions.length === 0
  // 按钮为什么不能点，要说出来。一个无解释的灰按钮会被当成故障。
  const exportHint = !runId
    ? '运行尚未创建'
    : !documentReady
      ? '结构化报告尚未就绪（运行结束后可用）'
      : undefined
  const tableExportHint =
    exportHint ?? (tableOptions.length === 0 ? '本次运行没有结构化表格' : undefined)

  useEffect(() => {
    if (tableOptions.length > 0 && !tableOptions.some((table) => table.id === selectedTableId)) {
      setSelectedTableId(tableOptions[0].id)
    }
  }, [selectedTableId, tableOptions])

  async function copy() {
    try {
      await navigator.clipboard.writeText(markdown)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  function downloadLocalMarkdown() {
    const short = runId ? `-${runId.slice(0, 8)}` : ''
    downloadText(`${slugify(query)}${short}.md`, markdown)
  }

  async function downloadMarkdown() {
    // 服务端版本含证据附录；没有 runId 或文档未就绪时回退到本地正文。
    if (!runId || !documentReady) {
      downloadLocalMarkdown()
      return
    }
    setExportError(null)
    setExporting('md')
    try {
      const result = await downloadRunDocument(runId, 'md', { includeHsiTables })
      downloadBlob(result.filename, result.blob)
    } catch {
      // 服务端导出失败不该让用户空手而归：正文就在手边，退化成本地下载，
      // 并说明这一份不含证据附录，而不是只弹一句"导出失败"。
      downloadLocalMarkdown()
      setExportError('服务端导出不可用，已下载不含证据附录的正文。')
    } finally {
      setExporting(null)
    }
  }

  async function exportDocument(format: RunDocumentFormat) {
    if (!runId) return
    setExportError(null)
    setExporting(format)
    try {
      const result = await downloadRunDocument(runId, format, {
        includeHsiTables,
        tableId:
          format === 'csv' || format === 'xlsx'
            ? selectedTableId || tableOptions[0]?.id
            : undefined,
      })
      downloadBlob(result.filename, result.blob)
    } catch (error: unknown) {
      setExportError(error instanceof Error ? error.message : '导出失败')
    } finally {
      setExporting(null)
    }
  }

  return (
    <div className="report-actions">
      <button type="button" className="btn ghost sm" onClick={copy} disabled={disabled}>
        <AppIcon name={copied ? 'check' : 'copy'} size={14} aria-hidden="true" />
        {copied ? '已复制' : '复制'}
      </button>
      <button
        type="button"
        className="btn ghost sm"
        onClick={() => void downloadMarkdown()}
        disabled={disabled || exporting !== null}
        aria-busy={exporting === 'md'}
      >
        <AppIcon name={exporting === 'md' ? 'loader' : 'download'} size={14} aria-hidden="true" />
        {exporting === 'md' ? '导出中…' : '下载 .md'}
      </button>
      {onTogglePreview && (
        <button
          type="button"
          className="btn ghost sm"
          onClick={onTogglePreview}
          disabled={disabled}
          aria-pressed={previewing}
        >
          <AppIcon name="file-search" size={14} aria-hidden="true" />
          {previewing ? '退出预览' : '打印预览'}
        </button>
      )}
      <button
        type="button"
        className="btn ghost sm"
        onClick={() => window.print()}
        disabled={disabled}
      >
        <AppIcon name="printer" size={14} aria-hidden="true" />
        打印 · 存为 PDF
      </button>
      {tableOptions.length > 1 && (
        <label className="report-export-table">
          <select
            aria-label="导出表格"
            value={selectedTableId}
            onChange={(event) => setSelectedTableId(event.target.value)}
            disabled={exporting !== null}
          >
            {tableOptions.map((table) => (
              <option value={table.id} key={table.id}>
                {table.label || table.id}
              </option>
            ))}
          </select>
        </label>
      )}
      <button
        type="button"
        className="btn ghost sm"
        onClick={() => void exportDocument('csv')}
        disabled={tableExportDisabled}
        aria-busy={exporting === 'csv'}
        title={tableExportHint}
      >
        <AppIcon name={exporting === 'csv' ? 'loader' : 'download'} size={14} aria-hidden="true" />
        {exporting === 'csv' ? '导出中…' : '下载 CSV'}
      </button>
      <button
        type="button"
        className="btn ghost sm"
        onClick={() => void exportDocument('xlsx')}
        disabled={tableExportDisabled}
        aria-busy={exporting === 'xlsx'}
        title={tableExportHint}
      >
        <AppIcon name={exporting === 'xlsx' ? 'loader' : 'download'} size={14} aria-hidden="true" />
        {exporting === 'xlsx' ? '导出中…' : '下载 XLSX'}
      </button>
      <button
        type="button"
        className="btn ghost sm"
        onClick={() => void exportDocument('pdf')}
        disabled={exportDisabled}
        aria-busy={exporting === 'pdf'}
        title={exportHint}
      >
        <AppIcon name={exporting === 'pdf' ? 'loader' : 'download'} size={14} aria-hidden="true" />
        {exporting === 'pdf' ? '导出中…' : '下载 PDF'}
      </button>
      {exportError && (
        <span className="report-export-error" role="alert">
          {exportError}
        </span>
      )}
    </div>
  )
}
