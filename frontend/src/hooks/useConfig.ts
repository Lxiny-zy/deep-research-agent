import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getConfig, updateConfig } from '../api/client'
import type { ConfigUpdate } from '../types'

export function useConfig() {
  return useQuery({ queryKey: ['config'], queryFn: ({ signal }) => getConfig(signal) })
}

export function useUpdateConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ConfigUpdate) => updateConfig(body),
    onSuccess: (data) => {
      window.dispatchEvent(new Event('dr:config-changed'))
      qc.setQueryData(['config'], data)
      qc.invalidateQueries({ queryKey: ['search-resource-impact'] })
    },
  })
}
