import { useId, useState, type DragEvent } from 'react'
import { AppIcon, type AppIconName } from './AppIcon'
import { humanSize } from '../lib/workbench'
import { DOCUMENT_LIMIT_LABEL } from '../lib/uploadLimits'
import { ACCEPTED_EXTENSIONS, MAX_ATTACHMENTS, type AttachmentItem } from '../hooks/useAttachments'

interface Props {
  items: AttachmentItem[]
  onAdd: (files: FileList | File[]) => void
  onRemove: (key: string) => void
  disabled?: boolean
  full?: boolean
  error?: string | null
}

const KIND_ICON: Record<string, AppIconName> = {
  pdf: 'file',
  docx: 'file',
  pptx: 'presentation',
  xlsx: 'chart',
  markdown: 'braces',
  text: 'file',
}

function kindLabel(item: AttachmentItem): string {
  const kind = item.summary?.kind ?? item.name.split('.').pop()?.toLowerCase() ?? ''
  return (
    { docx: 'Word', pptx: 'PPT', xlsx: 'Excel', markdown: 'MD', pdf: 'PDF', text: '文本' }[kind] ??
    kind.toUpperCase()
  )
}

/**
 * 任务附件区：拖放或点击上传，逐个显示解析状态（解析中 / 已就绪 + 片段数 / 失败原因）。
 * 模型会在检索前先阅读这些文件，结论同样经过逐字核验并在报告中标注文件与位置。
 */
export default function AttachmentDropzone({
  items,
  onAdd,
  onRemove,
  disabled,
  full,
  error,
}: Props) {
  const inputId = useId()
  const [dragging, setDragging] = useState(false)

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    if (disabled || full) return
    if (event.dataTransfer.files.length) onAdd(event.dataTransfer.files)
  }

  return (
    <div className="attach">
      <div
        className={`attach-zone${dragging ? ' is-dragging' : ''}${disabled || full ? ' is-disabled' : ''}`}
        onDragOver={(event) => {
          event.preventDefault()
          if (!disabled && !full) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <span className="attach-zone-icon" aria-hidden="true">
          <AppIcon name="download" size={18} className="attach-zone-arrow" />
        </span>
        <span className="attach-zone-copy">
          <strong>
            拖入文件，或
            <label htmlFor={inputId} className="attach-zone-pick">
              选择文件
            </label>
          </strong>
          <small>
            PDF、Word、PPT、Excel、Markdown、TXT、CSV · 单个 {DOCUMENT_LIMIT_LABEL} 以内 · 最多{' '}
            {MAX_ATTACHMENTS} 个
          </small>
        </span>
        <input
          id={inputId}
          type="file"
          multiple
          className="visually-hidden"
          aria-label="上传附件"
          accept={ACCEPTED_EXTENSIONS.join(',')}
          disabled={disabled || full}
          onChange={(event) => {
            if (event.target.files?.length) onAdd(event.target.files)
            event.target.value = ''
          }}
        />
      </div>
      {error && (
        <p role="alert" className="attach-item-error">
          {error}
        </p>
      )}
      {items.length > 0 && (
        <ul className="attach-list" aria-label="已添加的附件">
          {items.map((item) => (
            <li key={item.key} className={`attach-item is-${item.status}`}>
              <span className="attach-item-icon" aria-hidden="true">
                <AppIcon name={KIND_ICON[item.summary?.kind ?? ''] ?? 'file'} size={16} />
              </span>
              <span className="attach-item-body">
                <span className="attach-item-name" title={item.name}>
                  {item.name}
                </span>
                <span className="attach-item-meta">
                  <span className="attach-item-kind">{kindLabel(item)}</span>
                  {humanSize(item.size)}
                  {item.status === 'queued' && ' · 等待上传'}
                  {item.status === 'uploading' && ' · 正在解析…'}
                  {item.status === 'ready' &&
                    item.summary &&
                    ` · ${item.summary.chunk_count} 个片段${item.summary.truncated ? '（已截取）' : ''}`}
                  {item.status === 'error' && (
                    <span className="attach-item-error"> · {item.error}</span>
                  )}
                </span>
                {item.status === 'uploading' && (
                  <span className="attach-progress" aria-hidden="true" />
                )}
              </span>
              <span className="attach-item-state" aria-hidden="true">
                {item.status === 'uploading' && (
                  <AppIcon name="loader" size={15} className="spin" />
                )}
                {item.status === 'ready' && <AppIcon name="check-circle" size={15} />}
                {item.status === 'error' && <AppIcon name="alert" size={15} />}
              </span>
              <button
                type="button"
                className="btn btn-ghost btn-sm icon-button"
                aria-label={`移除 ${item.name}`}
                onClick={() => onRemove(item.key)}
              >
                <AppIcon name="x" size={14} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
