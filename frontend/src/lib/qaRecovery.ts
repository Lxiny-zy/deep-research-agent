import { getConversation } from '../api/client'
import { QaStreamInterruptedError, RequestTimeoutError } from '../api/transport'
import type { QaMessage } from '../types'

export const QA_RECOVERY_INTERVAL_MS = 1500
export const QA_RECOVERY_ATTEMPTS = 40

export function canRecoverQaAnswer(error: unknown): boolean {
  return error instanceof RequestTimeoutError || error instanceof QaStreamInterruptedError
}

/**
 * A browser or proxy timeout does not prove that the server stopped working.
 * Poll the durable conversation before asking the user to submit the question
 * again, so a late answer is shown exactly once.
 */
export async function recoverTimedOutAnswer(
  conversationId: string,
  query: string,
  baselineCount: number,
): Promise<QaMessage | null> {
  for (let attempt = 0; attempt < QA_RECOVERY_ATTEMPTS; attempt += 1) {
    if (attempt > 0) {
      await new Promise((resolve) => globalThis.setTimeout(resolve, QA_RECOVERY_INTERVAL_MS))
    }
    try {
      const conversation = await getConversation(conversationId)
      const candidate = conversation.messages
        .slice(baselineCount)
        .reverse()
        .find((message) => message.query === query)
      if (candidate) return candidate
    } catch {
      // A single poll can fail while the API is still finishing. Keep polling.
    }
  }
  return null
}
