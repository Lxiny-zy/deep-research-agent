import { useMutation, useQueryClient } from '@tanstack/react-query'
import { cancelQaRequest } from '../api/client'
import type { QaConversation, QaMessage } from '../types'

export function useCancelQaRequest(onStopped: (message: QaMessage, requestId: string) => void) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ conversationId, requestId }: { conversationId: string; requestId: string }) =>
      cancelQaRequest(conversationId, requestId),
    onSuccess: async (message, { conversationId, requestId }) => {
      client.setQueryData<QaConversation>(['qa-conversation', conversationId], (previous) =>
        previous
          ? {
              ...previous,
              messages: previous.messages.some((item) => item.id === message.id)
                ? previous.messages.map((item) => (item.id === message.id ? message : item))
                : [...previous.messages, message],
            }
          : previous,
      )
      onStopped(message, requestId)
      await client.invalidateQueries({ queryKey: ['qa-conversation', conversationId] })
      await client.invalidateQueries({ queryKey: ['qa-conversations'] })
    },
  })
}
