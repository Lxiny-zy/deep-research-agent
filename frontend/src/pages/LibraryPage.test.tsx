import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import LibraryPage from './LibraryPage'

const api = vi.hoisted(() => ({
  listProjects: vi.fn(async () => [{ id: 'p1', name: '项目', source_count: 0, corpus_count: 1 }]),
  listCorpora: vi.fn(async () => [{ id: 'c1', name: '主资料库', source_count: 0 }]),
  listLibrarySources: vi.fn(async () => []),
  listSourceChunks: vi.fn(async () => []),
  importLibrarySource: vi.fn(),
  createProject: vi.fn(),
  createCorpus: vi.fn(),
  deleteLibrarySource: vi.fn(),
  setLibrarySourceStatus: vi.fn(),
}))
vi.mock('../api/client', () => api)
vi.mock('../hooks/useConfig', () => ({
  useConfig: () => ({ data: { access: { role: 'admin' } } }),
}))

describe('LibraryPage batch import', () => {
  it('imports sequentially, keeps failures and retries only failed files', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { container } = render(
      <QueryClientProvider client={client}>
        <LibraryPage />
      </QueryClientProvider>,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'PDF' }))
    const files = ['one.pdf', 'two.pdf', 'three.pdf'].map((name) => {
      const file = new File(['%PDF'], name, { type: 'application/pdf' })
      Object.defineProperty(file, 'arrayBuffer', {
        value: async () => new TextEncoder().encode('%PDF').buffer,
      })
      return file
    })
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    expect(input.multiple).toBe(true)
    fireEvent.change(input, { target: { files } })
    let finishFirst!: (value: { id: string }) => void
    api.importLibrarySource
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finishFirst = resolve
          }),
      )
      .mockRejectedValueOnce(new Error('解析失败'))
      .mockResolvedValueOnce({ id: 's3' })
    fireEvent.click(screen.getByRole('button', { name: '导入资料库' }))
    await waitFor(() => expect(api.importLibrarySource).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: '正在提取与分块…' })).toBeDisabled()
    finishFirst({ id: 's1' })
    expect(
      await screen.findByText('成功导入 2 个文件，失败 1 个，可重试失败文件。'),
    ).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('two.pdf：解析失败')
    expect(api.importLibrarySource.mock.calls.map((call) => call[1].title)).toEqual([
      'one',
      'two',
      'three',
    ])
    api.importLibrarySource.mockResolvedValueOnce({ id: 's2' })
    fireEvent.click(screen.getByRole('button', { name: '导入资料库' }))
    await screen.findByText('成功导入 1 个文件，失败 0 个。')
    expect(api.importLibrarySource).toHaveBeenCalledTimes(4)
    expect(api.importLibrarySource.mock.calls[3][1].title).toBe('two')
  })
})
