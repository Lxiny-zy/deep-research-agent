import { useCallback, useRef, useState } from 'react'
import { uploadAttachment } from '../api/client'
import type { AttachmentPayload, AttachmentSummary } from '../types'

export const MAX_ATTACHMENTS = 8
export const MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024
export const ACCEPTED_EXTENSIONS = [
  '.pdf',
  '.docx',
  '.pptx',
  '.xlsx',
  '.md',
  '.markdown',
  '.txt',
  '.csv',
  '.tsv',
  '.json',
  '.tex',
  '.html',
]

export interface AttachmentItem {
  key: string
  name: string
  size: number
  status: 'uploading' | 'ready' | 'error'
  error?: string
  summary?: AttachmentSummary
  payload?: AttachmentPayload
}

export function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result ?? '')
      resolve(result.slice(result.indexOf(',') + 1))
    }
    reader.onerror = () => reject(reader.error ?? new Error('无法读取文件'))
    reader.readAsDataURL(file)
  })
}

function acceptable(file: File): string | null {
  const lower = file.name.toLowerCase()
  if (!ACCEPTED_EXTENSIONS.some((extension) => lower.endsWith(extension))) return '不支持的文件类型'
  if (file.size > MAX_ATTACHMENT_BYTES) return '文件超过 16 MB'
  if (file.size === 0) return '文件为空'
  return null
}

/** 任务附件：选择后立即上传解析，展示每个文件的解析结果；创建任务时提交已就绪的附件。 */
export function useAttachments() {
  const [items, setItems] = useState<AttachmentItem[]>([])
  const [selectionError, setSelectionError] = useState<string | null>(null)
  const counter = useRef(0)

  const update = useCallback((key: string, patch: Partial<AttachmentItem>) => {
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...patch } : item)))
  }, [])

  // 已有条目数用 ref 记录：add 在同一次渲染里可能被连续调用，读 state 会拿到旧值
  const countRef = useRef(0)
  countRef.current = items.length

  const upload = useCallback(
    async (key: string, file: File) => {
      try {
        const data = await toBase64(file)
        const result = await uploadAttachment({
          filename: file.name,
          mime_type: file.type,
          data_base64: data,
        })
        if (result.attachment.truncated || result.summary.truncated) {
          throw new Error('文件未完整解析，请移除后重新上传')
        }
        update(key, { status: 'ready', summary: result.summary, payload: result.attachment })
      } catch (error) {
        update(key, {
          status: 'error',
          error: error instanceof Error ? error.message : '解析失败',
        })
      }
    },
    [update],
  )

  const add = useCallback(
    (files: FileList | File[]) => {
      // 副作用（上传）放在 setState 之外：StrictMode 下 updater 会被调用两次
      const room = Math.max(0, MAX_ATTACHMENTS - countRef.current)
      if (files.length > room) {
        setSelectionError(`还可添加 ${room} 个文件，本次选择未添加；请减少选择的文件数量`)
        return
      }
      setSelectionError(null)
      const accepted = Array.from(files).map((file) => {
        counter.current += 1
        const problem = acceptable(file)
        const item: AttachmentItem = {
          key: `f${counter.current}`,
          name: file.name,
          size: file.size,
          status: problem ? 'error' : 'uploading',
          error: problem ?? undefined,
        }
        return { item, file, problem }
      })
      countRef.current += accepted.length
      setItems((current) => [...current, ...accepted.map((entry) => entry.item)])
      for (const entry of accepted) if (!entry.problem) void upload(entry.item.key, entry.file)
    },
    [upload],
  )

  const remove = useCallback((key: string) => {
    setItems((current) => current.filter((item) => item.key !== key))
    setSelectionError(null)
  }, [])

  const clear = useCallback(() => {
    setItems([])
    setSelectionError(null)
  }, [])

  const ready = items.filter((item) => item.status === 'ready' && item.payload)
  return {
    items,
    add,
    remove,
    clear,
    payloads: ready.map((item) => item.payload as AttachmentPayload),
    uploading: items.some((item) => item.status === 'uploading'),
    full: items.length >= MAX_ATTACHMENTS,
    selectionError,
  }
}
