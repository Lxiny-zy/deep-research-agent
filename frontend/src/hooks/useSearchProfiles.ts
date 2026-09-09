import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  deleteSearchProfile,
  listSearchProfiles,
  saveSearchProfile,
  testSearchProfile,
  getSearchResourceImpact,
} from '../api/client'
import type { SearchProfileInput } from '../types'

export function useSearchProfiles() {
  return useQuery({ queryKey: ['search-profiles'], queryFn: listSearchProfiles })
}

export function useSearchResourceImpact() {
  return useQuery({
    queryKey: ['search-resource-impact'],
    queryFn: getSearchResourceImpact,
    refetchOnMount: 'always',
    staleTime: 0,
  })
}

export function useSearchProfileMutations() {
  const qc = useQueryClient()
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['search-profiles'] })
    qc.invalidateQueries({ queryKey: ['agents'] })
    qc.invalidateQueries({ queryKey: ['search-resource-impact'] })
  }
  return {
    save: useMutation({
      mutationFn: ({ body, id }: { body: SearchProfileInput; id?: string }) =>
        saveSearchProfile(body, id),
      onSuccess: invalidate,
    }),
    remove: useMutation({ mutationFn: deleteSearchProfile, onSuccess: invalidate }),
    test: useMutation({ mutationFn: testSearchProfile }),
  }
}
