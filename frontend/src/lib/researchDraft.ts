import type { ResearchParams } from '../types'

export interface DraftContent {
  query: string
  params: ResearchParams
  workflow: string
}

export interface ResearchDraft extends DraftContent {
  savedAt: number
  context: string
}

const numericFields = {
  max_sub_questions: [1, 12],
  max_rounds: [0, 5],
  max_concurrency: [1, 16],
  results_per_search: [1, 15],
} as const

function draftStorage(context: string) {
  // Follow-up drafts belong to their tab's conversation; standalone questions survive reopening.
  return context ? sessionStorage : localStorage
}

function draftKey(context: string) {
  return context ? 'dr_followup_draft' : 'dr_research_draft'
}

export function loadResearchDraft(context = ''): ResearchDraft | null {
  try {
    const raw = draftStorage(context).getItem(draftKey(context))
    if (!raw) return null
    const value = JSON.parse(raw)
    if (
      !value ||
      value.context !== context ||
      typeof value.query !== 'string' ||
      !value.query.trim()
    )
      return null
    if (typeof value.savedAt !== 'number' || !Number.isFinite(value.savedAt)) return null
    const params: ResearchParams = {}
    for (const [key, [min, max]] of Object.entries(numericFields)) {
      const number = value.params?.[key]
      if (
        typeof number === 'number' &&
        Number.isInteger(number) &&
        number >= min &&
        number <= max
      ) {
        params[key as keyof typeof numericFields] = number
      }
    }
    if (typeof value.params?.require_corroboration === 'boolean')
      params.require_corroboration = value.params.require_corroboration
    return {
      query: value.query,
      params,
      workflow: typeof value.workflow === 'string' ? value.workflow : '',
      savedAt: value.savedAt,
      context,
    }
  } catch {
    return null
  }
}

export function saveResearchDraft(draft: DraftContent, context = ''): boolean {
  try {
    if (!draft.query.trim()) return clearResearchDraft(context)
    draftStorage(context).setItem(
      draftKey(context),
      JSON.stringify({ ...draft, savedAt: Date.now(), context }),
    )
    return true
  } catch {
    return false
  }
}

export function clearResearchDraft(context = ''): boolean {
  try {
    draftStorage(context).removeItem(draftKey(context))
    return true
  } catch {
    return false
  }
}
