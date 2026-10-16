import { useCallback, useEffect, useRef, useState } from 'react'

const drafts = new Map<string, string>()
let generation = 0
if (typeof window !== 'undefined') {
  window.addEventListener('dr:credentials-cleared', () => {
    generation += 1
    drafts.clear()
    try {
      for (const key of Object.keys(sessionStorage)) {
        if (key.startsWith('dr_qa_draft_')) sessionStorage.removeItem(key)
      }
    } catch { /* Storage can be disabled by browser policy. */ }
  })
}

function read(key: string): string {
  if (drafts.has(key)) return drafts.get(key)!
  try { return sessionStorage.getItem(key) ?? '' } catch { return '' }
}

function save(key: string, text: string) {
  if (text) drafts.set(key, text)
  else drafts.delete(key)
  try {
    if (text) sessionStorage.setItem(key, text)
    else sessionStorage.removeItem(key)
  } catch { /* Keep the draft in this tab when storage is unavailable. */ }
}

/** Unsent input is independent of durable requests and their late responses. */
export function useQaDraft(scope: string) {
  const key = `dr_qa_draft_${scope}`
  const moving = useRef<{ from: string; to: string } | null>(null)
  const state = useRef({ key, text: read(key), revision: 0, generation, lineage: {} })
  const [, render] = useState(0)
  // A newly created conversation changes scope without changing the draft's
  // identity. Preserve that identity until React receives the new route/id.
  if (moving.current && key !== moving.current.from) moving.current = null
  const effectiveKey = moving.current?.to ?? key
  if (state.current.key !== effectiveKey || state.current.generation !== generation) {
    state.current = {
      key: effectiveKey, text: read(effectiveKey), revision: state.current.revision + 1,
      generation, lineage: {},
    }
  }
  useEffect(() => {
    const clear = () => {
      moving.current = null
      state.current = {
        key, text: '', revision: state.current.revision + 1, generation, lineage: {},
      }
      render(v => v + 1)
    }
    window.addEventListener('dr:credentials-cleared', clear)
    return () => window.removeEventListener('dr:credentials-cleared', clear)
  }, [key])
  const setDraft = useCallback((text: string) => {
    state.current.text = text
    state.current.revision += 1
    save(state.current.key, text)
    render(v => v + 1)
  }, [])
  const capture = () => ({ ...state.current })
  const restore = (text: string, snapshot: ReturnType<typeof capture> | undefined) => {
    const current = state.current
    if (snapshot && snapshot.lineage === current.lineage && snapshot.revision === current.revision &&
        snapshot.generation === generation && !current.text) setDraft(text)
  }
  const moveTo = (nextScope: string) => {
    const nextKey = `dr_qa_draft_${nextScope}`
    if (nextKey === state.current.key) return
    if (state.current.generation !== generation) return
    if (state.current.key !== key && moving.current?.from !== key) return
    const previousKey = state.current.key
    // Keep a destination draft if it already exists; a failure from the old
    // submission must not replace that saved input either.
    const saved = read(nextKey)
    if (saved && !state.current.text) {
      state.current.text = saved
      state.current.revision += 1
    }
    save(nextKey, state.current.text)
    save(previousKey, '')
    moving.current = { from: key, to: nextKey }
    state.current.key = nextKey
  }
  return { draft: state.current.text, setDraft, capture, restore, moveTo }
}
