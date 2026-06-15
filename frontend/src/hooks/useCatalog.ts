import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createAgent,
  createModel,
  createSearchKey,
  deleteAgent,
  deleteModel,
  deleteSearchKey,
  listAgents,
  listBehaviors,
  listModels,
  listSearchKeys,
  updateAgent,
  updateModel,
  updateSearchKey,
} from '../api/client'
import type { AgentCardInput, ModelProfileInput, SearchKeyInput } from '../types'

// ── 模型档案 ──────────────────────────────────────────────────────────
export function useModels() {
  return useQuery({ queryKey: ['models'], queryFn: listModels })
}

export function useModelMutations() {
  const qc = useQueryClient()
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['models'] })
    qc.invalidateQueries({ queryKey: ['agents'] }) // 卡片视图带出绑定模型名
  }
  return {
    create: useMutation({ mutationFn: (b: ModelProfileInput) => createModel(b), onSuccess: invalidate }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: ModelProfileInput }) => updateModel(id, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({ mutationFn: (id: string) => deleteModel(id), onSuccess: invalidate }),
  }
}

// ── 角色卡片 ──────────────────────────────────────────────────────────
export function useAgents() {
  return useQuery({ queryKey: ['agents'], queryFn: listAgents })
}

export function useBehaviors() {
  return useQuery({ queryKey: ['behaviors'], queryFn: listBehaviors, staleTime: Infinity })
}

export function useAgentMutations() {
  const qc = useQueryClient()
  const invalidate = () => qc.invalidateQueries({ queryKey: ['agents'] })
  return {
    create: useMutation({ mutationFn: (b: AgentCardInput) => createAgent(b), onSuccess: invalidate }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: AgentCardInput }) => updateAgent(id, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({ mutationFn: (id: string) => deleteAgent(id), onSuccess: invalidate }),
  }
}

// ── 搜索 key 池 ───────────────────────────────────────────────────────
export function useSearchKeys() {
  return useQuery({ queryKey: ['search-keys'], queryFn: listSearchKeys })
}

export function useSearchKeyMutations() {
  const qc = useQueryClient()
  const invalidate = () => qc.invalidateQueries({ queryKey: ['search-keys'] })
  return {
    create: useMutation({ mutationFn: (b: SearchKeyInput) => createSearchKey(b), onSuccess: invalidate }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: SearchKeyInput }) => updateSearchKey(id, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({ mutationFn: (id: string) => deleteSearchKey(id), onSuccess: invalidate }),
  }
}
