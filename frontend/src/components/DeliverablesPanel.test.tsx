import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import DeliverablesPanel from './DeliverablesPanel'

const getDeliverables = vi.hoisted(() => vi.fn())
vi.mock('../api/client', async () => ({
  ...(await vi.importActual('../api/client')),
  getDeliverables,
}))

it('retries initial generation only after an explicit action', async () => {
  const registry = { content_version: 'new' }
  getDeliverables.mockResolvedValue(registry)
  const onUpdated = vi.fn()
  render(
    <DeliverablesPanel
      runId="run-1"
      registry={undefined}
      loading={false}
      error={new Error('暂时无法写入文件')}
      onUpdated={onUpdated}
    />,
  )
  expect(getDeliverables).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '重新尝试生成' }))
  await waitFor(() =>
    expect(getDeliverables).toHaveBeenCalledWith('run-1', undefined, expect.any(String)),
  )
  expect(onUpdated).toHaveBeenCalledWith(registry)
})
