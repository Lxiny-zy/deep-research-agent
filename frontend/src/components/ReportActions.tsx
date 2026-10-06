import { useEffect, useState } from 'react'
import {
  downloadRenderResult,
  getCapabilities,
  type LatexTemplateName,
  type RunDocumentFormat,
} from '../api/client'
import { downloadBlob, downloadText, slugify } from '../lib/download'
import { AppIcon } from './AppIcon'
import { useRenderOperation } from '../hooks/useRenderOperation'
import { offlineReport } from '../lib/offlineReport'

// 报告操作：复制 / 下载 .md / 打印预览 / 打印·存为 PDF，以及服务端结构化导出。
//
// 服务端导出固定预览版本并保存操作回执。未知版本只允许有明确标记的离线正文；
// 版本冲突和连接失败保留错误与重连入口，不自动下载另一种文件。
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
  contentVersion,
  previewing = false,
  onTogglePreview,
  capabilities,
  supportFailed = false,
}: {
  markdown: string
  query: string
  runId?: string
  includeHsiTables?: boolean
  tableOptions?: { id: string; label: string }[]
  /** 结构化文档是否已就绪。未就绪时服务端导出只会产出空文件，不如不给点。 */
  documentReady?: boolean
  contentVersion?: string
  previewing?: boolean
  onTogglePreview?: () => void
  capabilities?: Partial<Record<RunDocumentFormat, boolean>>
  supportFailed?: boolean
}) {
  const [copied, setCopied] = useState(false)
  const [selectedTableId, setSelectedTableId] = useState('')
  const [exportError, setExportError] = useState<string | null>(null)
  const operation = useRenderOperation(runId, 'export', async (receipt) => {
    if (!runId) return
    const result = await downloadRenderResult(runId, receipt)
    downloadBlob(result.filename, result.blob)
  })
  const exporting = operation.busy
  const [latexTemplate, setLatexTemplate] = useState<LatexTemplateName>('ctexart')
  const [available, setAvailable] = useState(capabilities)
  useEffect(() => {
    if (capabilities) {
      setAvailable(capabilities)
      return
    }
    const controller = new AbortController()
    getCapabilities(controller.signal)
      .then((result) => setAvailable(result.exports))
      .catch(() => {
        if (!controller.signal.aborted)
          setAvailable({
            pdf: false,
            xlsx: false,
            tex: false,
            bib: false,
            bundle: false,
            paper_pdf: false,
          })
      })
    return () => controller.abort()
  }, [capabilities])
  const disabled = !markdown
  // 导出依赖服务端已装配好的文档。只判 runId 的话，运行仍在流式阶段时按钮就是
  // 可点的，而那时导出的 CSV 是空表、PDF 是空正文——用户拿到一个"成功"的空文件，
  // 比按钮暂时不可点更难理解。
  const exportDisabled = !runId || !documentReady || !contentVersion || exporting
  // 单表导出还需要真的有表。非 HSI 运行通常一张表都没有，此时 CSV/XLSX 无意义。
  const tableExportDisabled = exportDisabled || tableOptions.length === 0
  // 按钮为什么不能点，要说出来。一个无解释的灰按钮会被当成故障。
  const exportHint = !runId
    ? '运行尚未创建'
    : !documentReady || !contentVersion
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
    downloadText(
      `${slugify(query)}${short}-offline.md`,
      offlineReport(markdown, { runId, version: contentVersion, supportFailed }),
    )
  }

  async function downloadMarkdown() {
    // 服务端版本含证据附录；没有 runId 或文档未就绪时回退到本地正文。
    if (!runId || !documentReady || !contentVersion) {
      downloadLocalMarkdown()
      return
    }
    await exportDocument('md')
  }

  async function exportDocument(format: RunDocumentFormat) {
    if (!runId || !contentVersion) return
    setExportError(null)
    await operation.start({
      kind: 'export',
      format,
      version: contentVersion,
      include_hsi_tables: includeHsiTables,
      table_id:
        format === 'csv' || format === 'xlsx' ? selectedTableId || tableOptions[0]?.id : undefined,
      ...(format === 'tex' || format === 'paper_pdf' || format === 'bundle'
        ? { profile: 'academic' as const, template: latexTemplate }
        : {}),
    })
  }

  function exportItem(
    format: RunDocumentFormat,
    label: string,
    options: { disabled: boolean; title?: string; busyLabel?: string; icon?: 'download' | 'file' },
  ) {
    const busy = exporting
    const failedReview = supportFailed && (format === 'pdf' || format === 'paper_pdf')
    return (
      <button
        type="button"
        className="run-export-item"
        onClick={() => void exportDocument(format)}
        disabled={options.disabled || failedReview}
        aria-busy={busy}
        title={failedReview ? '正文结论依据尚未通过核验' : options.title}
      >
        <AppIcon
          name={busy ? 'loader' : (options.icon ?? 'download')}
          size={14}
          className={busy ? 'spin' : ''}
          aria-hidden="true"
        />
        {busy ? (options.busyLabel ?? '导出中…') : label}
      </button>
    )
  }

  return (
    <div className="report-actions">
      <button type="button" className="btn btn-ghost btn-sm" onClick={copy} disabled={disabled}>
        <AppIcon name={copied ? 'check' : 'copy'} size={14} aria-hidden="true" />
        {copied ? '已复制' : '复制'}
      </button>
      {onTogglePreview && (
        <button
          type="button"
          className="btn btn-ghost btn-sm"
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
        className="btn btn-ghost btn-sm"
        onClick={() => window.print()}
        disabled={disabled}
      >
        <AppIcon name="printer" size={14} aria-hidden="true" />
        打印 · 存为 PDF
      </button>
      <details className="run-export-menu">
        <summary className="btn btn-secondary btn-sm">
          <AppIcon name="download" size={14} aria-hidden="true" />
          导出
          <AppIcon name="chevron-down" size={14} aria-hidden="true" />
        </summary>
        <div className="run-export-panel" role="group" aria-label="导出格式">
          <span className="run-export-group">报告</span>
          <button
            type="button"
            className="run-export-item"
            onClick={() => void downloadMarkdown()}
            disabled={disabled || exporting}
            aria-busy={exporting}
          >
            <AppIcon
              name={exporting ? 'loader' : 'download'}
              size={14}
              className={exporting ? 'spin' : ''}
              aria-hidden="true"
            />
            {exporting ? '导出中…' : '下载 .md'}
          </button>
          {exportItem('pdf', '下载 PDF', {
            disabled: exportDisabled || !available?.pdf,
            title: !available?.pdf ? '此服务暂不支持 PDF 导出，可使用打印功能' : exportHint,
          })}
          <span className="run-export-group">表格</span>
          {tableOptions.length > 1 && (
            <select
              className="input run-export-select"
              aria-label="导出表格"
              value={selectedTableId}
              onChange={(event) => setSelectedTableId(event.target.value)}
              disabled={exporting}
            >
              {tableOptions.map((table) => (
                <option value={table.id} key={table.id}>
                  {table.label || table.id}
                </option>
              ))}
            </select>
          )}
          {exportItem('csv', '下载 CSV', { disabled: tableExportDisabled, title: tableExportHint })}
          {exportItem('xlsx', '下载 XLSX', {
            disabled: tableExportDisabled || !available?.xlsx,
            title: !available?.xlsx ? '此服务暂不支持 XLSX 导出' : tableExportHint,
          })}
          <span className="run-export-group">科研交付</span>
          <label className="run-export-template">
            <span>论文模板</span>
            <select
              className="input run-export-select"
              value={latexTemplate}
              onChange={(event) => setLatexTemplate(event.target.value as LatexTemplateName)}
              disabled={exporting}
            >
              <option value="ctexart">中文论文 · ctexart</option>
              <option value="ctexrep">中文长文 · ctexrep</option>
              <option value="ieeetran">IEEE · 单栏审稿稿</option>
              <option value="acmart">ACM · manuscript</option>
            </select>
          </label>
          {exportItem('paper_pdf', '学术排版 PDF', {
            disabled: exportDisabled || available?.paper_pdf !== true,
            title:
              available?.paper_pdf !== true
                ? '学术排版 PDF 需要服务端安装 TeX Live 与 latexmk，可先下载 .tex 源文件'
                : exportHint,
            busyLabel: '编译中…',
            icon: 'file',
          })}
          {exportItem('tex', '下载 .tex', {
            disabled: exportDisabled || available?.tex !== true,
            title: available?.tex !== true ? '此服务暂不支持 LaTeX 源文件导出' : exportHint,
          })}
          {exportItem('bib', '下载 .bib', {
            disabled: exportDisabled || available?.bib !== true,
            title: available?.bib !== true ? '此服务暂不支持 BibTeX 导出' : exportHint,
          })}
          {exportItem('bundle', '下载复现包', {
            disabled: exportDisabled || available?.bundle !== true,
            title: available?.bundle !== true ? '此服务暂不支持科研复现包导出' : exportHint,
          })}
        </div>
      </details>
      {contentVersion && (
        <span className="muted small" title={contentVersion}>
          内容版本 {contentVersion.slice(0, 12)}
        </span>
      )}
      {(exportError || operation.error) && (
        <span className="report-export-error" role="alert">
          {exportError || operation.error} 可重新连接已提交的操作，或刷新页面获取当前版本。
          <button type="button" onClick={() => void operation.reconnect()} disabled={exporting}>
            重新连接导出
          </button>
          <button type="button" onClick={downloadLocalMarkdown}>
            下载离线正文副本
          </button>
        </span>
      )}
    </div>
  )
}
