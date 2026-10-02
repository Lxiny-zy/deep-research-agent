import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import AttachmentDropzone from './AttachmentDropzone'
import { useAttachments } from '../hooks/useAttachments'

// 失败路径用普通函数而不是 vi.fn：vi.fn 会为结果追踪额外订阅一次 rejected promise，
// 被测代码已经 catch 的拒绝会被误报为未处理异常。
const mocks = vi.hoisted(() => ({ uploadAttachment: vi.fn(), failure: null as Error | null }))
vi.mock('../api/client', () => ({
  uploadAttachment: (...args: unknown[]) =>
    mocks.failure ? Promise.reject(mocks.failure) : mocks.uploadAttachment(...args),
}))

function file(name: string, body = 'content', type = 'text/plain') {
  return new File([body], name, { type })
}

describe('useAttachments', () => {
  beforeEach(() => {
    mocks.uploadAttachment.mockReset()
    mocks.failure = null
  })

  it('uploads accepted files and exposes ready payloads', async () => {
    mocks.uploadAttachment.mockResolvedValue({
      attachment: { id: 'a1', filename: 'notes.md' },
      summary: {
        id: 'a1',
        filename: 'notes.md',
        kind: 'markdown',
        mime_type: 'text/markdown',
        size: 7,
        char_count: 7,
        chunk_count: 1,
        truncated: false,
        preview: 'content',
        sections: [],
      },
    })
    const { result } = renderHook(() => useAttachments())
    act(() => result.current.add([file('notes.md')]))
    expect(result.current.uploading).toBe(true)
    await waitFor(() => expect(result.current.payloads).toHaveLength(1))
    expect(mocks.uploadAttachment).toHaveBeenCalledWith(
      expect.objectContaining({ filename: 'notes.md', data_base64: expect.any(String) }),
    )
    expect(result.current.uploading).toBe(false)
  })

  it('rejects unsupported files without uploading and reports server errors', async () => {
    mocks.failure = new Error('文档中没有可提取的文字')
    const { result } = renderHook(() => useAttachments())
    act(() => result.current.add([file('tool.exe'), file('scan.pdf', '%PDF', 'application/pdf')]))
    expect(result.current.items[0]).toMatchObject({ status: 'error', error: '不支持的文件类型' })
    await waitFor(() => expect(result.current.items[1].status).toBe('error'))
    expect(result.current.items[1].error).toBe('文档中没有可提取的文字')
    expect(result.current.payloads).toHaveLength(0)
  })

  it('rejects an excessive selection as a whole instead of silently losing later files', () => {
    const { result } = renderHook(() => useAttachments())
    act(() => result.current.add(Array.from({ length: 9 }, (_, index) => file(`${index}.txt`))))
    expect(result.current.items).toHaveLength(0)
    expect(result.current.selectionError).toContain('本次选择未添加')
    expect(mocks.uploadAttachment).not.toHaveBeenCalled()
  })

  it('keeps a truncated server response as an error, never a ready attachment', async () => {
    mocks.uploadAttachment.mockResolvedValue({
      attachment: { id: 'partial', truncated: true },
      summary: { truncated: true },
    })
    const { result } = renderHook(() => useAttachments())
    act(() => result.current.add([file('paper.txt')]))
    await waitFor(() => expect(result.current.items[0].status).toBe('error'))
    expect(result.current.items[0].error).toContain('未完整解析')
    expect(result.current.payloads).toHaveLength(0)
  })
})

describe('AttachmentDropzone', () => {
  it('lists files with status and lets the user remove them', () => {
    const onRemove = vi.fn()
    const onAdd = vi.fn()
    render(
      <AttachmentDropzone
        items={[
          { key: 'k1', name: '实验记录.docx', size: 2048, status: 'uploading' },
          { key: 'k2', name: 'bad.exe', size: 10, status: 'error', error: '不支持的文件类型' },
        ]}
        onAdd={onAdd}
        onRemove={onRemove}
      />,
    )
    expect(screen.getByText(/正在解析/)).toBeInTheDocument()
    expect(screen.getByText(/不支持的文件类型/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '移除 bad.exe' }))
    expect(onRemove).toHaveBeenCalledWith('k2')
    fireEvent.change(screen.getByLabelText('上传附件'), {
      target: { files: [file('a.pdf')] },
    })
    expect(onAdd).toHaveBeenCalled()
  })
})
