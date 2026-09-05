import { useEffect, useRef, useState } from 'react'
import {
  clearResearchDraft,
  loadResearchDraft,
  saveResearchDraft,
  type DraftContent,
} from '../lib/researchDraft'

export function useResearchDraft(defaultQuery: string, context: string) {
  const [initial] = useState(() => loadResearchDraft(context))
  const [draft, setDraft] = useState<DraftContent>(
    () => initial ?? { query: defaultQuery, workflow: '', params: {} },
  )
  const [status, setStatus] = useState<
    'idle' | 'restored' | 'saving' | 'saved' | 'unavailable' | 'cleared'
  >(initial ? 'restored' : 'idle')
  const [undo, setUndo] = useState<DraftContent | null>(null)
  const current = useRef(draft)
  const dirty = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    const flush = () => {
      clearTimeout(timer.current)
      if (dirty.current) saveResearchDraft(current.current, context)
    }
    window.addEventListener('pagehide', flush)
    return () => {
      flush()
      window.removeEventListener('pagehide', flush)
    }
  }, [context])

  function persist() {
    clearTimeout(timer.current)
    const saved = saveResearchDraft(current.current, context)
    if (saved) dirty.current = false
    setStatus(saved ? (current.current.query.trim() ? 'saved' : 'cleared') : 'unavailable')
  }

  function update(next: Partial<DraftContent>, edited = true) {
    const value = { ...current.current, ...next }
    current.current = value
    setDraft(value)
    if (!edited) return
    dirty.current = true
    setUndo(null)
    setStatus('saving')
    clearTimeout(timer.current)
    timer.current = setTimeout(persist, 350)
  }

  function discard() {
    clearTimeout(timer.current)
    dirty.current = false
    const cleared = clearResearchDraft(context)
    setStatus(cleared ? 'cleared' : 'unavailable')
    return cleared
  }

  function clear() {
    setUndo(current.current)
    discard()
    update({ query: '', params: {} }, false)
  }

  function restore() {
    if (!undo) return
    update(undo)
    persist()
  }

  return {
    ...draft,
    status,
    update,
    clear,
    discard,
    restore,
    canUndo: Boolean(undo),
    retry: persist,
  }
}
