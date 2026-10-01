import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { DeliverableRegistry } from '../types'
import {
  getDeliverables,
  getNarrative,
  getRunTemplate,
  getWorkspace,
  getUsage,
  getQualitySchema,
  listTiers,
  listTemplates,
  previewContract,
} from '../api/client'

export function useTemplates() {
  return useQuery({
    queryKey: ['templates'],
    queryFn: ({ signal }) => listTemplates(signal),
    staleTime: 5 * 60_000,
  })
}

/** 任务契约预览：输入停顿 400ms 后再请求，避免每敲一个字都打一次接口。 */
export function useContractPreview(
  template: string,
  query: string,
  enabled: boolean,
  strategy?: string | null,
) {
  const trimmed = query.trim()
  return useQuery({
    queryKey: ['contract-preview', template, trimmed, strategy ?? null],
    queryFn: async ({ signal }) => {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, 400)
        signal.addEventListener('abort', () => {
          clearTimeout(timer)
          reject(signal.reason)
        })
      })
      return previewContract(template, trimmed, signal, strategy)
    },
    enabled: enabled && Boolean(template) && trimmed.length > 0,
    staleTime: 60_000,
    retry: false,
  })
}

export function useDeliverables(runId: string | undefined, finished: boolean) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ['deliverables', runId],
    queryFn: ({ signal }) => getDeliverables(runId as string, signal),
    enabled: Boolean(runId) && finished,
    staleTime: 5 * 60_000,
    retry: false,
  })
  return {
    ...query,
    setRegistry: (registry: DeliverableRegistry) => {
      client.setQueryData(['deliverables', runId], registry)
      void client.invalidateQueries({ queryKey: ['workspace', runId] })
    },
  }
}

export function useRunTemplate(runId: string | undefined) {
  return useQuery({
    queryKey: ['run-template', runId],
    queryFn: ({ signal }) => getRunTemplate(runId as string, signal),
    enabled: Boolean(runId),
    staleTime: 30_000,
    retry: false,
  })
}

/** 人话进度：运行中每 5 秒刷新一次，终态后停止轮询。 */
export function useNarrative(runId: string | undefined, live: boolean) {
  return useQuery({
    queryKey: ['narrative', runId],
    queryFn: ({ signal }) => getNarrative(runId as string, signal),
    enabled: Boolean(runId),
    refetchInterval: live ? 5000 : false,
    retry: false,
  })
}

/** 步骤与产物文件树：运行中每 6 秒刷新，终态后停止。 */
export function useWorkspace(runId: string | undefined, live: boolean) {
  return useQuery({
    queryKey: ['workspace', runId],
    queryFn: ({ signal }) => getWorkspace(runId as string, signal),
    enabled: Boolean(runId),
    refetchInterval: live ? 6000 : false,
    retry: false,
  })
}

export function useTiers() {
  return useQuery({
    queryKey: ['tiers'],
    queryFn: ({ signal }) => listTiers(signal),
    staleTime: 10 * 60_000,
  })
}

export function useUsage() {
  return useQuery({
    queryKey: ['usage'],
    queryFn: ({ signal }) => getUsage(signal),
    staleTime: 30_000,
    retry: false,
  })
}

export function useQualitySchema() {
  return useQuery({
    queryKey: ['quality-schema'],
    queryFn: ({ signal }) => getQualitySchema(signal),
    staleTime: 30 * 60_000,
  })
}
