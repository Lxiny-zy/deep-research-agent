import { useQuery } from '@tanstack/react-query'
import { getRun, listRuns } from '../api/client'
import type { RunDetail } from '../types'

export function useRunsList(params: { limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: ['runs', params],
    queryFn: () => listRuns(params),
  })
}

type RefetchInterval =
  | number
  | false
  | ((query: { state: { data?: RunDetail } }) => number | false)

export function useRunDetail(
  id: string | undefined,
  opts?: { refetchInterval?: RefetchInterval },
) {
  return useQuery({
    queryKey: ['run', id],
    queryFn: () => getRun(id as string),
    enabled: Boolean(id),
    refetchInterval: opts?.refetchInterval ?? false,
  })
}
