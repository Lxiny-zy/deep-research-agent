import { useCallback, useEffect, useRef, useState } from 'react'
import { uploadAttachment } from '../api/client'
import type { AttachmentPayload, AttachmentSummary } from '../types'
import { DOCUMENT_LIMIT_LABEL, DOCUMENT_MAX_BYTES } from '../lib/uploadLimits'

export const MAX_ATTACHMENTS = 8
export const MAX_ATTACHMENT_BYTES = DOCUMENT_MAX_BYTES
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
  status: 'queued' | 'uploading' | 'ready' | 'error'
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
  if (file.size > MAX_ATTACHMENT_BYTES) return `文件超过 ${DOCUMENT_LIMIT_LABEL}`
  if (file.size === 0) return '文件为空'
  return null
}

/** 任务附件：选择后立即上传解析，展示每个文件的解析结果；创建任务时提交已就绪的附件。 */
export function useAttachments() {
  const [items, setItems] = useState<AttachmentItem[]>([])
  const [selectionError, setSelectionError] = useState<string | null>(null)
  const counter = useRef(0)
  const queue = useRef(Promise.resolve())
  const wanted = useRef(new Set<string>())
  const controllers = useRef(new Map<string, AbortController>())

  useEffect(() => {
    const pending = wanted.current
    const active = controllers.current
    return () => {
      pending.clear()
      for (const controller of active.values()) controller.abort()
      active.clear()
    }
  }, [])

  const update = useCallback((key: string, patch: Partial<AttachmentItem>) => {
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...patch } : item)))
  }, [])

  // 已有条目数用 ref 记录：add 在同一次渲染里可能被连续调用，读 state 会拿到旧值
  const countRef = useRef(0)
  countRef.current = items.length

  const upload = useCallback(
    async (key: string, file: File) => {
      if (!wanted.current.has(key)) return
      update(key, { status: 'uploading' })
      const controller = new AbortController()
      controllers.current.set(key, controller)
      try {
        const result = await uploadAttachment(file, controller.signal)
        if (result.attachment.truncated || result.summary.truncated) {
          throw new Error('文件未完整解析，请移除后重新上传')
        }
        update(key, { status: 'ready', summary: result.summary, payload: result.attachment })
      } catch (error) {
        update(key, {
          status: 'error',
          error: error instanceof Error ? error.message : '解析失败',
        })
      } finally {
        controllers.current.delete(key)
        wanted.current.delete(key)
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
          status: problem ? 'error' : 'queued',
          error: problem ?? undefined,
        }
        return { item, file, problem }
      })
      countRef.current += accepted.length
      setItems((current) => [...current, ...accepted.map((entry) => entry.item)])
      for (const entry of accepted) {
        if (entry.problem) continue
        wanted.current.add(entry.item.key)
        queue.current = queue.current.then(() => upload(entry.item.key, entry.file))
      }
    },
    [upload],
  )

  const remove = useCallback((key: string) => {
    wanted.current.delete(key)
    controllers.current.get(key)?.abort()
    setItems((current) => current.filter((item) => item.key !== key))
    setSelectionError(null)
  }, [])

  const clear = useCallback(() => {
    wanted.current.clear()
    for (const controller of controllers.current.values()) controller.abort()
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
    uploading: items.some((item) => item.status === 'uploading' || item.status === 'queued'),
    full: items.length >= MAX_ATTACHMENTS,
    selectionError,
  }
}
