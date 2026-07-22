import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { WorkflowDef } from '../types'
import { useWorkflowMutations } from './useCatalog'

const mocks = vi.hoisted(() => ({
  updateCustomWorkflow: vi.fn(),
}))

vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  updateCustomWorkflow: mocks.updateCustomWorkflow,
}))

const workflow = (version: number): WorkflowDef => ({
  id: 'wf-1',
  name: 'alpha',
  display_name: 'Alpha',
  description: '',
  steps: [],
  nodes: [],
  edges: [],
  viewport: { x: 0, y: 0, zoom: 1 },
  version,
  enabled: true,
})

describe('useWorkflowMutations', () => {
  it('updates the cached workflow with the server version after save', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    queryClient.setQueryData(['custom-workflows'], [workflow(1)])
    const updated = workflow(2)
    mocks.updateCustomWorkflow.mockResolvedValueOnce(updated)
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useWorkflowMutations(), { wrapper })
    await act(async () => {
      await result.current.update.mutateAsync({ id: 'wf-1', body: { version: 1 } })
    })

    expect(queryClient.getQueryData<WorkflowDef[]>(['custom-workflows'])).toEqual([updated])
  })
})
