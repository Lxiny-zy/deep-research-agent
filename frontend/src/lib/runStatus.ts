import type { TerminalRunStatus } from '../types'

/** A stopped run may still require content review or delivery repairs. */
export function isTerminalRunStatus(
  status: string | null | undefined,
): status is TerminalRunStatus {
  return (
    status === 'done' || status === 'needs_review' || status === 'error' || status === 'cancelled'
  )
}
