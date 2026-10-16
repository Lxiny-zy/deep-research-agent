import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { clearReaderPdfCache } from '../lib/readerPdfCache'
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
    queryFn: ({ signal }) => listRuns(params, signal),
  })
}

export function useTags() {
  return useQuery({ queryKey: ['tags'], queryFn: listTags })
}

type RefetchInterval = number | false | ((query: { state: { data?: RunDetail } }) => number | false)

export function useRunDetail(id: string | undefined, opts?: { refetchInterval?: RefetchInterval }) {
  return useQuery({
    queryKey: ['run', id],
    queryFn: ({ signal }) => getRun(id as string, signal),
    enabled: Boolean(id),
    refetchInterval: opts?.refetchInterval ?? false,
  })
}

export function useRunDocument(
  id: string | undefined,
  opts: { enabled?: boolean; includeHsiTables?: boolean; version?: string } = {},
) {
  const includeHsiTables = opts.includeHsiTables ?? false
  return useQuery({
    queryKey: ['run-document', id, { includeHsiTables, version: opts.version ?? 'current' }],
    queryFn: ({ signal }) =>
      getRunDocument(id as string, { includeHsiTables, version: opts.version, signal }),
    enabled: Boolean(id) && (opts.enabled ?? true),
    staleTime: opts.version ? Infinity : 0,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  })
}

export function useDeleteRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteRun(id),
    onSuccess: (_, id) => {
      clearDeletedRun(qc, id)
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
    onSuccess: (result, ids) => {
      const deleted = result.deleted_ids ?? (result.skipped === 0 ? ids : [])
      deleted.forEach(id => clearDeletedRun(qc, id))
      qc.invalidateQueries({ queryKey: ['runs'] })
      qc.invalidateQueries({ queryKey: ['tags'] })
    },
  })
}

function clearDeletedRun(client: QueryClient, id: string) {
  const domains = new Set([
    'run', 'run-document', 'reader', 'deliverables', 'run-template', 'workspace', 'narrative',
  ])
  client.removeQueries({ predicate: query =>
    domains.has(String(query.queryKey[0])) && query.queryKey[1] === id,
  })
  // Conversation caches can contain task material even after their parent
  // disappears. Drop these snapshots and let active observers reload.
  client.removeQueries({ queryKey: ['qa-conversation'] })
  void client.invalidateQueries({ queryKey: ['qa-conversations'] })
  void client.invalidateQueries({ queryKey: ['operations'] })
  void clearReaderPdfCache()
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
