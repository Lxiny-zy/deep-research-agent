import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ContractPreview from './ContractPreview'
import DeliverablesPanel from './DeliverablesPanel'
import RunTaskSummary from './RunTaskSummary'
import TaskTemplatePicker from './TaskTemplatePicker'
import type { DeliverableRegistry, TaskContract, TaskTemplate } from '../types'

const mocks = vi.hoisted(() => ({ fetchDeliverable: vi.fn(), downloadBlob: vi.fn() }))
vi.mock('../api/client', () => ({ fetchDeliverable: mocks.fetchDeliverable }))
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
