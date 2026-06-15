import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getConfig, updateConfig } from '../api/client'
import type { ConfigUpdate } from '../types'

export function useConfig() {
  return useQuery({ queryKey: ['config'], queryFn: getConfig })
}

export function useUpdateConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ConfigUpdate) => updateConfig(body),
    onSuccess: (data) => qc.setQueryData(['config'], data),
  })
}
