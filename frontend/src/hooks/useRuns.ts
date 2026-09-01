import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  batchDeleteRuns,
  cancelRun,
  deleteRun,
  getRun,
  getRunDocument,
  listRuns,
  listTags,
  resumeRun,
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

type RefetchInterval = number | false | ((query: { state: { data?: RunDetail } }) => number | false)

export function useRunDetail(id: string | undefined, opts?: { refetchInterval?: RefetchInterval }) {
  return useQuery({
    queryKey: ['run', id],
    queryFn: () => getRun(id as string),
    enabled: Boolean(id),
    refetchInterval: opts?.refetchInterval ?? false,
  })
}

export function useRunDocument(
  id: string | undefined,
  opts: { enabled?: boolean; includeHsiTables?: boolean } = {},
) {
  const includeHsiTables = opts.includeHsiTables ?? false
  return useQuery({
    queryKey: ['run-document', id, { includeHsiTables }],
    queryFn: () => getRunDocument(id as string, { includeHsiTables }),
    enabled: Boolean(id) && (opts.enabled ?? true),
    // A completed report is immutable for the lifetime of a run. Keeping it
    // cached avoids rebuilding the structured document on every tab revisit.
    staleTime: Infinity,
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

export function useCancelRun(id: string | undefined) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => cancelRun(id as string),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['run', id] })
      qc.invalidateQueries({ queryKey: ['runs'] })
    },
  })
}

export function useResumeRun(id: string | undefined) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => resumeRun(id as string),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['run', id] })
      // A resumed run can replace a partial report under the same id. The
      // document query is otherwise immutable/cached forever, so explicitly
      // mark the previous export payload stale for the new attempt.
      qc.invalidateQueries({ queryKey: ['run-document', id] })
      qc.invalidateQueries({ queryKey: ['runs'] })
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
