import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  batchDeleteRuns,
  deleteRun,
  getRun,
  listRuns,
  listTags,
  setTags,
} from '../api/client'
import type { RunDetail } from '../types'

export interface RunsFilter {
  limit?: number
  offset?: number
  status?: string
  q?: string
  tag?: string
}

export function useRunsList(params: RunsFilter = {}) {
  return useQuery({
    queryKey: ['runs', params],
    queryFn: () => listRuns(params),
  })
}

export function useTags() {
  return useQuery({ queryKey: ['tags'], queryFn: listTags })
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

export function useDeleteRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteRun(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['runs'] })
      qc.invalidateQueries({ queryKey: ['tags'] })
    },
  })
}

export function useBatchDeleteRuns() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (ids: string[]) => batchDeleteRuns(ids),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['runs'] })
      qc.invalidateQueries({ queryKey: ['tags'] })
    },
  })
}

export function useSetTags(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (tags: string[]) => setTags(id, tags),
    onSuccess: (detail) => {
      qc.setQueryData(['run', id], detail)
      qc.invalidateQueries({ queryKey: ['runs'] })
      qc.invalidateQueries({ queryKey: ['tags'] })
    },
  })
}
