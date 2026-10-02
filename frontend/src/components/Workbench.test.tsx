import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ContractPreview from './ContractPreview'
import DeliverablesPanel from './DeliverablesPanel'
import RunTaskSummary from './RunTaskSummary'
import TaskTemplatePicker from './TaskTemplatePicker'
import type { DeliverableRegistry, TaskContract, TaskTemplate } from '../types'

const mocks = vi.hoisted(() => ({
  fetchDeliverable: vi.fn(),
  retryDeliverable: vi.fn(),
  reviseRunContent: vi.fn(),
  getDeliverables: vi.fn(),
  downloadBlob: vi.fn(),
}))
vi.mock('../api/client', () => ({
  fetchDeliverable: mocks.fetchDeliverable,
  retryDeliverable: mocks.retryDeliverable,
  reviseRunContent: mocks.reviseRunContent,
  getDeliverables: mocks.getDeliverables,
}))
vi.mock('../lib/download', () => ({ downloadBlob: mocks.downloadBlob }))

function template(overrides: Partial<TaskTemplate> = {}): TaskTemplate {
  return {
    key: 'peerReview',
    title: '同行评审',
    tagline: '五段式评审',
    description: '',
    icon: 'shield',
    workflow: 'peer_review',
    input_kind: 'paper',
    input_label: '待评审论文',
    input_placeholder: '粘贴 arXiv 链接',
    sections: [],
    deliverables: ['md', 'pdf'],
    examples: [],
    accepts_attachments: true,
    tier_default: 'standard',
    min_citations: 1,
    tags: [],
    ...overrides,
  }
}

const contract: TaskContract = {
  template: 'peerReview',
  title: '同行评审：x',
  original_request: 'x',
  focus: '重点看实验',
  papers: [],
  dataset_csv: '',
  required_sections: ['论文摘要', '优点'],
  deliverables: ['md', 'pdf'],
  constraints: [],
  evidence_rules: [],
  tier: 'standard',
}

describe('TaskTemplatePicker', () => {
  it('renders a radio group and reports the selected template', () => {
    const onChange = vi.fn()
    render(
      <TaskTemplatePicker
        templates={[template({ key: 'autoResearch', title: '深度研究' }), template()]}
        value="autoResearch"
        onChange={onChange}
      />,
    )
    const radios = screen.getAllByRole('radio')
    expect(radios).toHaveLength(2)
    expect(radios[0]).toBeChecked()
    fireEvent.click(screen.getByLabelText(/同行评审/))
    expect(onChange).toHaveBeenCalledWith('peerReview')
    expect(screen.getAllByText('PDF').length).toBeGreaterThan(0)
  })
})

describe('ContractPreview', () => {
  it('warns when a paper task has no recognisable paper link', () => {
    render(
      <ContractPreview template={template()} contract={contract} loading={false} error={null} />,
    )
    expect(screen.getByText(/还没有论文/)).toBeInTheDocument()
    expect(screen.getByText('重点看实验')).toBeInTheDocument()
    expect(screen.getByText('论文摘要')).toBeInTheDocument()
  })

  it('lists recognised papers', () => {
    render(
      <ContractPreview
        template={template()}
        contract={{
          ...contract,
          papers: [{ kind: 'arxiv', value: '2205.10102', url: 'https://arxiv.org/abs/2205.10102' }],
        }}
        loading={false}
        error={null}
      />,
    )
    expect(screen.getByText('2205.10102')).toBeInTheDocument()
    expect(screen.queryByText(/未识别到/)).not.toBeInTheDocument()
  })
})

const registry: DeliverableRegistry = {
  version: 1,
  run_id: 'r1',
  template: 'peerReview',
  title: '评审',
  status: 'warn',
  generated_at: '2026-09-26T00:00:00Z',
  primary: 'r.pdf',
  items: [
    {
      name: 'r.pdf',
      format: 'pdf',
      title: '评审（PDF）',
      role: 'report',
      size: 2048,
      sha256: 'a',
      mime_type: 'application/pdf',
      status: 'pass',
      issues: [],
    },
    {
      name: 'r.docx',
      format: 'docx',
      title: '评审（Word）',
      role: 'report',
      size: 4096,
      sha256: 'b',
      mime_type: 'application/octet-stream',
      status: 'pass',
      issues: [],
    },
  ],
  gates: [
    { name: 'citation', status: 'pass', issues: [], metrics: { used: 14, required: 20 } },
    { name: 'structure', status: 'warn', issues: ['缺少章节：总体推荐'], metrics: {} },
    {
      name: 'scholarly',
      status: 'warn',
      issues: ['口语化或夸张措辞「说白了」', '（建议）超长句（超过 160 字），建议拆分'],
      metrics: {},
    },
    { name: 'revision', status: 'pass', issues: [], metrics: { revisions: 2 } },
  ],
}

describe('DeliverablesPanel', () => {
  it('starts a separate content revision while retaining the original file registry', async () => {
    const created = vi.fn()
    mocks.reviseRunContent.mockResolvedValue({ run_id: 'revision-child' })
    const failed = {
      ...registry,
      status: 'fail' as const,
      content_revision: { available: true, reason: '', source_version: 'a'.repeat(64) },
    }
    render(
      <DeliverablesPanel
        runId="original"
        registry={failed}
        loading={false}
        error={null}
        onRevisionCreated={created}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: '继续修订内容' }))
    await waitFor(() => expect(created).toHaveBeenCalledWith('revision-child'))
    expect(mocks.reviseRunContent).toHaveBeenCalledWith('original', 'a'.repeat(64))
    expect(screen.getByText('评审（PDF）')).toBeInTheDocument()
  })

  it('does not offer content revision to a read-only user', () => {
    render(
      <DeliverablesPanel
        runId="original"
        registry={{
          ...registry,
          can_retry: false,
          content_revision: { available: true, reason: '', source_version: 'a'.repeat(64) },
        }}
        loading={false}
        error={null}
        onRevisionCreated={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: '继续修订内容' })).not.toBeInTheDocument()
  })

  it('refreshes the offered source version after a content conflict', async () => {
    const updated = vi.fn()
    const original = {
      ...registry,
      content_revision: { available: true, reason: '', source_version: 'a'.repeat(64) },
    }
    const next = {
      ...original,
      content_revision: { ...original.content_revision, source_version: 'b'.repeat(64) },
    }
    mocks.reviseRunContent.mockRejectedValue(
      Object.assign(new Error('版本已更新'), { status: 409 }),
    )
    mocks.getDeliverables.mockResolvedValue(next)
    render(
      <DeliverablesPanel
        runId="original"
        registry={original}
        loading={false}
        error={null}
        onUpdated={updated}
        onRevisionCreated={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: '继续修订内容' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('版本已更新')
    await waitFor(() => expect(updated).toHaveBeenCalledWith(next))
  })

  it('retries the failed format and reuses its request ID after a connection failure', async () => {
    const failed = {
      ...registry,
      content_version: 'a'.repeat(64),
      failures: [{ format: 'pdf', title: 'PDF', issues: ['temporary failure'], retryable: true }],
    }
    const updated = { ...failed, content_version: 'b'.repeat(64), failures: [] }
    const onUpdated = vi.fn()
    mocks.retryDeliverable
      .mockRejectedValueOnce(new Error('connection lost'))
      .mockResolvedValueOnce(updated)
    render(
      <DeliverablesPanel
        runId="r1"
        registry={failed}
        loading={false}
        error={null}
        onUpdated={onUpdated}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: '重新生成 PDF' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('connection lost')
    fireEvent.click(screen.getByRole('button', { name: '重新生成 PDF' }))
    await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(updated))
    expect(mocks.retryDeliverable.mock.calls[0]).toEqual(mocks.retryDeliverable.mock.calls[1])
    expect(mocks.retryDeliverable.mock.calls[0].slice(0, 3)).toEqual([
      'r1',
      failed.content_version,
      'pdf',
    ])
  })

  it('does not offer export retries for blocked content or read-only access', () => {
    render(
      <DeliverablesPanel
        runId="r1"
        registry={{
          ...registry,
          content_version: 'a'.repeat(64),
          can_retry: false,
          failures: [
            { format: 'pdf', title: 'PDF', issues: ['content review failed'], retryable: false },
          ],
        }}
        loading={false}
        error={null}
        onUpdated={vi.fn()}
      />,
    )
    expect(screen.getByText('content review failed')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新生成 PDF' })).not.toBeInTheDocument()
  })

  it('shows the primary deliverable, gate verdicts and downloads with auth', async () => {
    mocks.fetchDeliverable.mockResolvedValue({ blob: new Blob(['x']), filename: 'r.docx' })
    render(<DeliverablesPanel runId="r1" registry={registry} loading={false} error={null} />)
    expect(screen.getByText('评审（PDF）')).toBeInTheDocument()
    // 「需关注」的交付如实表述为部分完成
    expect(screen.getByText('部分完成 · 有待改进项')).toBeInTheDocument()
    expect(screen.getByText('缺少章节：总体推荐')).toBeInTheDocument()
    // 质量概览：引用数对照下限、返工次数
    const summary = screen.getByLabelText('质量概览')
    expect(summary).toHaveTextContent('14 / 要求 20')
    expect(summary).toHaveTextContent('2 次')
    // 硬性问题与改进建议分开列出
    expect(screen.getByText('口语化或夸张措辞「说白了」')).toBeInTheDocument()
    expect(screen.getByLabelText('学术质量改进建议')).toHaveTextContent('超长句')
    fireEvent.click(screen.getByRole('button', { name: '下载 评审（Word）' }))
    await waitFor(() => expect(mocks.downloadBlob).toHaveBeenCalledWith('r.docx', expect.any(Blob)))
    expect(mocks.fetchDeliverable).toHaveBeenCalledWith('r1', 'r.docx')
  })

  it('surfaces download failures', async () => {
    mocks.fetchDeliverable.mockRejectedValue(new Error('网络错误'))
    render(<DeliverablesPanel runId="r1" registry={registry} loading={false} error={null} />)
    fireEvent.click(screen.getByRole('button', { name: /^下载$/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('网络错误')
  })
})

describe('RunTaskSummary', () => {
  it('links a revision back to the preserved original task', () => {
    render(
      <RunTaskSummary
        info={{
          template: template(),
          contract,
          extras: {},
          analysis: null,
          intake: null,
          revision_source: { parent_run_id: 'original', source_version: 'v' },
        }}
      />,
    )
    expect(screen.getByRole('link', { name: '查看原任务' })).toHaveAttribute(
      'href',
      '/runs/original',
    )
  })
  it('shows the review score and intake failures', () => {
    render(
      <RunTaskSummary
        info={{
          template: template(),
          contract,
          extras: { score: 7 },
          analysis: null,
          intake: { papers: [], sections: [], failures: [{ url: 'u', error: '网络不可达' }] },
        }}
      />,
    )
    expect(screen.getByLabelText('评审评分 7 分（满分 10 分）')).toBeInTheDocument()
    expect(screen.getByText(/网络不可达/)).toBeInTheDocument()
  })

  it('renders nothing for legacy deep-research runs without a contract', () => {
    const { container } = render(
      <RunTaskSummary
        info={{
          template: template({ key: 'autoResearch' }),
          contract: null,
          extras: {},
          analysis: null,
          intake: null,
        }}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
