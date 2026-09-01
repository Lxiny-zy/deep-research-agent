import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { downloadRunDocument } from '../api/client'
import { downloadBlob, downloadText } from '../lib/download'
import ReportActions from './ReportActions'

// 报告操作栏的契约：
// - 服务端导出必须等结构化文档就绪，否则用户会拿到"成功"的空文件；
// - 单表导出还要求真的有表；
// - 下载 .md 优先服务端（带证据附录），失败时退化成本地正文并说明退化了什么。

vi.mock('../api/client', () => ({ downloadRunDocument: vi.fn() }))
vi.mock('../lib/download', () => ({
  downloadBlob: vi.fn(),
  downloadText: vi.fn(),
  slugify: (s: string) => s,
}))

const downloadRunDocumentMock = vi.mocked(downloadRunDocument)
const downloadBlobMock = vi.mocked(downloadBlob)
const downloadTextMock = vi.mocked(downloadText)

const TABLES = [{ id: 'hsi_reconstruction', label: '重建算法' }]

function renderActions(over: Partial<Parameters<typeof ReportActions>[0]> = {}) {
  return render(
    <ReportActions
      markdown="# 报告"
      query="q"
      runId="run-1"
      documentReady
      tableOptions={TABLES}
      {...over}
    />,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  downloadRunDocumentMock.mockResolvedValue({
    blob: new Blob(['x']),
    filename: 'research-run-1.md',
  })
})

describe('ReportActions 导出闸门', () => {
  it('结构化文档未就绪时禁用服务端导出，而不是让用户导出空文件', () => {
    renderActions({ documentReady: false })

    expect(screen.getByRole('button', { name: /下载 CSV/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /下载 XLSX/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /下载 PDF/ })).toBeDisabled()
  })

  it('没有表格时禁用单表导出，但整份文档的 PDF 仍可导出', () => {
    renderActions({ tableOptions: [] })

    expect(screen.getByRole('button', { name: /下载 CSV/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /下载 XLSX/ })).toBeDisabled()
    // PDF 渲染的是整份文档（正文 + 证据附录），没有表也有内容
    expect(screen.getByRole('button', { name: /下载 PDF/ })).toBeEnabled()
  })

  it('文档就绪且有表时，CSV 带上所选表 id', async () => {
    renderActions()

    await userEvent.click(screen.getByRole('button', { name: /下载 CSV/ }))

    expect(downloadRunDocumentMock).toHaveBeenCalledWith('run-1', 'csv', {
      includeHsiTables: false,
      tableId: 'hsi_reconstruction',
    })
    expect(downloadBlobMock).toHaveBeenCalled()
  })

  it('PDF 不带 table_id——它渲染整份文档，带上会声明一个该端点不遵守的约束', async () => {
    renderActions()

    await userEvent.click(screen.getByRole('button', { name: /下载 PDF/ }))

    expect(downloadRunDocumentMock).toHaveBeenCalledWith('run-1', 'pdf', {
      includeHsiTables: false,
      tableId: undefined,
    })
  })

  it('导出失败时把服务端 detail 呈现出来，而不是笼统的"失败"', async () => {
    downloadRunDocumentMock.mockRejectedValue(new Error("install the optional 'pdf' extra"))
    renderActions()

    await userEvent.click(screen.getByRole('button', { name: /下载 PDF/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent("install the optional 'pdf' extra")
  })
})

describe('ReportActions 下载 .md', () => {
  it('文档就绪时走服务端，拿到的是含证据附录的那一份', async () => {
    renderActions()

    await userEvent.click(screen.getByRole('button', { name: /下载 \.md/ }))

    expect(downloadRunDocumentMock).toHaveBeenCalledWith('run-1', 'md', {
      includeHsiTables: false,
    })
    expect(downloadTextMock).not.toHaveBeenCalled()
  })

  it('没有 runId 时直接用本地正文，不发请求', async () => {
    renderActions({ runId: undefined, documentReady: false })

    await userEvent.click(screen.getByRole('button', { name: /下载 \.md/ }))

    expect(downloadRunDocumentMock).not.toHaveBeenCalled()
    expect(downloadTextMock).toHaveBeenCalledWith('q.md', '# 报告')
  })

  it('服务端导出失败时退化成本地正文，并说明这一份缺了什么', async () => {
    downloadRunDocumentMock.mockRejectedValue(new Error('boom'))
    renderActions()

    await userEvent.click(screen.getByRole('button', { name: /下载 \.md/ }))

    // 用户仍然拿到文件
    expect(downloadTextMock).toHaveBeenCalled()
    // 且被告知拿到的不是完整版
    expect(await screen.findByRole('alert')).toHaveTextContent('不含证据附录')
  })

  it('正文为空时不可点——没有可下载的东西', () => {
    renderActions({ markdown: '' })

    expect(screen.getByRole('button', { name: /下载 \.md/ })).toBeDisabled()
  })
})
