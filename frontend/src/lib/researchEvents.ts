import type { ResearchEvent } from '../types'

/** Streaming payloads belong to the report/reasoning views, not the activity log. */
export function isActivityEvent(event: ResearchEvent): boolean {
  // Failures and terminal events must remain visible even if they carry model data.
  if (event.type === 'error' || event.type === 'done' || event.type === 'cancelled') return true
  return (
    event.type !== 'token' &&
    event.type !== 'report' &&
    typeof event.data?.reasoning_delta !== 'string' &&
    event.data?.llm_usage == null
  )
}

/** Keep one reasoning record per call so fragments cannot evict real progress. */
export function appendResearchEvent(events: ResearchEvent[], next: ResearchEvent): ResearchEvent[] {
  const data = next.data
  if (
    next.type === 'info' &&
    typeof data?.reasoning_delta === 'string' &&
    typeof data.call_id === 'string'
  ) {
    const index = events.findIndex(
      (event) =>
        event.type === 'info' &&
        event.stage === next.stage &&
        event.data?.call_id === data.call_id &&
        typeof event.data?.reasoning_delta === 'string',
    )
    if (index >= 0) {
      return events.map((event, position) =>
        position === index
          ? {
              ...event,
              data: {
                ...event.data,
                ...data,
                reasoning_delta: String(event.data?.reasoning_delta) + data.reasoning_delta,
              },
            }
          : event,
      )
    }
  }
  return [...events.slice(-4999), next]
}
