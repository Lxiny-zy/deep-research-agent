import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createCorpus,
  createProject,
  deleteLibrarySource,
  importLibrarySource,
  listCorpora,
  listLibrarySources,
  listProjects,
  listSourceChunks,
  setLibrarySourceStatus,
} from '../api/client'
import type { ImportSourceInput, LibrarySourceStatus } from '../types'

export function useProjects() {
  return useQuery({ queryKey: ['projects'], queryFn: ({ signal }) => listProjects(signal) })
}

export function useCreateProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: createProject,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['projects'] }),
  })
}

export function useCorpora(projectId: string | undefined) {
  return useQuery({
    queryKey: ['projects', projectId, 'corpora'],
    queryFn: ({ signal }) => listCorpora(projectId as string, signal),
    enabled: Boolean(projectId),
  })
}

export function useCreateCorpus(projectId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: { name: string; description?: string }) =>
      createCorpus(projectId as string, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'corpora'] })
    },
  })
}

export function useLibrarySources(projectId: string | undefined, corpusId?: string) {
  return useQuery({
    queryKey: ['projects', projectId, 'sources', corpusId ?? 'all'],
    queryFn: ({ signal }) => listLibrarySources(projectId as string, corpusId, signal),
    enabled: Boolean(projectId),
  })
}

export function useSourceChunks(projectId: string | undefined, sourceId: string | undefined) {
  return useQuery({
    queryKey: ['projects', projectId, 'sources', sourceId, 'chunks'],
    queryFn: ({ signal }) => listSourceChunks(projectId as string, sourceId as string, signal),
    enabled: Boolean(projectId && sourceId),
  })
}

export function useImportSource(projectId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ImportSourceInput) => importLibrarySource(projectId as string, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'sources'] })
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'corpora'] })
    },
  })
}

export function useSetSourceStatus(projectId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ sourceId, status }: { sourceId: string; status: LibrarySourceStatus }) =>
      setLibrarySourceStatus(projectId as string, sourceId, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'sources'] })
    },
  })
}

export function useDeleteLibrarySource(projectId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sourceId: string) => deleteLibrarySource(projectId as string, sourceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'sources'] })
      queryClient.invalidateQueries({ queryKey: ['projects', projectId, 'corpora'] })
    },
  })
}
