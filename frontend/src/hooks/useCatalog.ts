import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createAgent,
  createCustomWorkflow,
  createModel,
  createSearchKey,
  deleteAgent,
  deleteCustomWorkflow,
  deleteModel,
  deleteSearchKey,
  listAgents,
  listBehaviors,
  listCustomWorkflows,
  listModels,
  listRoles,
  listSearchKeys,
  testModel,
  testModelConfig,
  discoverModels,
  testSearchKey,
  updateAgent,
  updateCustomWorkflow,
  updateModel,
  updateSearchKey,
} from '../api/client'
import type {
  AgentCardInput,
  ModelProfileInput,
  SearchKeyInput,
  WorkflowDefInput,
} from '../types'

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

// 「测试连接」：不 invalidate 缓存，结果由调用方按 id 持有
export function useTestModel() {
  return useMutation({ mutationFn: (id: string) => testModel(id) })
}

export function useModelProbe() {
  return {
    test: useMutation({ mutationFn: testModelConfig }),
    discover: useMutation({ mutationFn: discoverModels }),
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

export function useTestSearchKey() {
  return useMutation({ mutationFn: (id: string) => testSearchKey(id) })
}

// ── 自定义工作流（构建器）──────────────────────────────────────────────
export function useRoles() {
  return useQuery({ queryKey: ['roles'], queryFn: listRoles, staleTime: 60_000 })
}

export function useCustomWorkflows() {
  return useQuery({ queryKey: ['custom-workflows'], queryFn: listCustomWorkflows })
}

export function useWorkflowMutations() {
  const qc = useQueryClient()
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['custom-workflows'] })
    qc.invalidateQueries({ queryKey: ['workflows'] }) // 新建研究的选择器列表也要刷新
  }
  return {
    create: useMutation({
      mutationFn: (b: WorkflowDefInput) => createCustomWorkflow(b),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({ id, body }: { id: string; body: WorkflowDefInput }) =>
        updateCustomWorkflow(id, body),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: string) => deleteCustomWorkflow(id),
      onSuccess: invalidate,
    }),
  }
}
