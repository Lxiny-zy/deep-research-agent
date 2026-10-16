import { useInfiniteQuery } from '@tanstack/react-query'
import { listConversations } from '../api/client'

export function useConversations(runId?: string, enabled = true) {
  const query = useInfiniteQuery({
    queryKey: ['qa-conversations', 'pages', runId ?? null],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) => listConversations(signal, runId, { limit: 30, before: pageParam }),
    enabled,
    getNextPageParam: (last) => {
      const tail = last.at(-1)
      return last.length === 30 && tail?.updated_at ? `${tail.updated_at}|${tail.id}` : undefined
    },
  })
  const items = query.data?.pages.flat() ?? []
  // A conversation updated while paging can move to the first page.
  const data = [...new Map(items.map(item => [item.id, item])).values()]
  return { ...query, data }
}
