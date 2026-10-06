import { act, renderHook, waitFor } from '@testing-library/react'
import { useAttachments } from './useAttachments'

const upload = vi.hoisted(() => vi.fn())
vi.mock('../api/client', () => ({ uploadAttachment: upload }))

it('retains the original PDF only locally and drops region selections with a removed attachment', async () => {
  const file = new File(['pdf bytes'], 'paper.pdf', { type: 'application/pdf' })
  upload.mockResolvedValue({
    attachment: { id: 'attachment-id', filename: file.name },
    summary: { kind: 'pdf', truncated: false },
  })
  const { result } = renderHook(() => useAttachments())
  act(() => result.current.add([file]))
  await waitFor(() => expect(result.current.items[0].status).toBe('ready'))
  expect(result.current.items[0].file).toBe(file)
  act(() =>
    result.current.setImageRegions(result.current.items[0].key, [
      { page: 2, figure_label: 'Fig. 1', bounds: [0.1, 0.2, 0.5, 0.7] },
    ]),
  )
  expect(result.current.payloads[0].image_regions).toEqual([
    { page: 2, figure_label: 'Fig. 1', bounds: [0.1, 0.2, 0.5, 0.7] },
  ])
  expect(result.current.payloads[0]).not.toHaveProperty('file')
  act(() => result.current.remove(result.current.items[0].key))
  expect(result.current.items).toEqual([])
  expect(result.current.payloads).toEqual([])
})
