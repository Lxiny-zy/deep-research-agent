import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Finding } from '../types'
import ReportView from './ReportView'

// 可审计报告：[n] 引用可点击 → 证据侧栏（论断/逐字 quote/验证徽章/哈希缩写/矛盾链接）；
// 报告头部证据链概览条；流式无 findings 时优雅降级为不可点击角标。

function makeFinding(over: {
  statement: string
  source_url: string
  evidence_quote: string
  claim_id: string
  status?: 'unverified' | 'verified'
  consistency_status?: 'not_checked' | 'clear' | 'conflicted'
  contradicts_claim_ids?: string[]
  contradiction_reason?: string
  source_content_hash?: string
  source_title?: string
  evidence_context?: string
}): Finding {
  return {
    statement: over.statement,
    source_url: over.source_url,
    evidence_quote: over.evidence_quote,
    confidence: 0.9,
    verification: {
      status: over.status ?? 'verified',
      method: 'normalized_quote',
      source_content_hash: over.source_content_hash ?? 'deadbeefcafebabe0123456789',
      source_title: over.source_title,
      evidence_context: over.evidence_context,
      reason: '',
      semantic_status: 'supported',
      semantic_confidence: 0.9,
      semantic_reason: '',
      claim_id: over.claim_id,
      consistency_status: over.consistency_status ?? 'clear',
      contradicts_claim_ids: over.contradicts_claim_ids ?? [],
      contradiction_reason: over.contradiction_reason ?? '',
    },
  }
}

const MARKDOWN = [
  '# 结论',
  '',
  'GPU 出货量创新高 [1]，但整机功耗持续上升 [2]。',
  '',
  '## 参考来源',
  '[1] https://a.example.com/report',
  '[2] https://b.example.com/power',
  '',
].join('\n')

const CITATIONS = ['https://a.example.com/report', 'https://b.example.com/power']

const FINDINGS: Finding[] = [
  makeFinding({
    statement: 'GPU 出货量创下历史新高',
    source_url: 'https://a.example.com/report',
    evidence_quote: 'GPU shipments hit a record high in Q4',
    claim_id: 'claim-1',
    source_content_hash: 'abcdef1234567890fedcba',
    source_title: 'GPU Market Quarterly',
    evidence_context:
      'After several flat quarters, GPU shipments hit a record high in Q4 as demand recovered.',
  }),
  makeFinding({
    statement: '整机功耗持续上升',
    source_url: 'https://b.example.com/power',
    evidence_quote: 'total system power draw keeps climbing',
    claim_id: 'claim-2',
    consistency_status: 'conflicted',
    contradicts_claim_ids: ['claim-3'],
    contradiction_reason: '两来源对功耗趋势结论相反',
  }),
  makeFinding({
    statement: '新工艺下整机功耗明显下降',
    source_url: 'https://c.example.com/efficiency',
    evidence_quote: 'power consumption dropped notably',
    claim_id: 'claim-3',
    status: 'unverified',
  }),
]

describe('ReportView 可审计证据链', () => {
  it('点击 [1] 打开证据侧栏并显示检索上下文、原文匹配状态与哈希缩写', async () => {
    const user = userEvent.setup()
    render(
      <ReportView
        markdown={MARKDOWN}
        streaming={false}
        findings={FINDINGS}
        citations={CITATIONS}
      />,
    )

    // 正文与参考来源列表里各有一个 [1] 角标，点第一个（正文）
    const [cite1] = screen.getAllByRole('button', { name: '查看引用 1 的证据' })
    await user.click(cite1)

    const drawer = within(screen.getByRole('dialog', { name: '引用 1 的证据' }))
    expect(drawer.getByText(/After several flat quarters/)).toBeInTheDocument()
    expect(drawer.getByText('GPU shipments hit a record high in Q4')).toHaveProperty(
      'tagName',
      'MARK',
    )
    expect(drawer.getByText('GPU 出货量创下历史新高')).toBeInTheDocument()
    expect(drawer.getByText('GPU Market Quarterly')).toBeInTheDocument()
    expect(drawer.getByText('原文匹配')).toBeInTheDocument()
    expect(drawer.getByText('语义支持 · 模型判定')).toBeInTheDocument()
    expect(drawer.getByText('未检测到冲突')).toBeInTheDocument()
    expect(drawer.getByText(/hash abcdef1234/)).toBeInTheDocument()
  })

  it('切换引用时保持侧栏打开、更新来源并把证据列表滚动位置复位', async () => {
    const user = userEvent.setup()
    render(
      <ReportView
        markdown={MARKDOWN}
        streaming={false}
        findings={FINDINGS}
        citations={CITATIONS}
      />,
    )

    const [cite1] = screen.getAllByRole('button', { name: '查看引用 1 的证据' })
    await user.click(cite1)
    expect(cite1).toHaveAttribute('aria-expanded', 'true')

    const body = screen.getByRole('dialog').querySelector('.evidence-drawer-body')
    expect(body).not.toBeNull()
    if (body) body.scrollTop = 200

    const [cite2] = screen.getAllByRole('button', { name: '查看引用 2 的证据' })
    await user.click(cite2)

    expect(screen.getByRole('dialog', { name: '引用 2 的证据' })).toBeInTheDocument()
    expect(screen.getByText('整机功耗持续上升')).toBeInTheDocument()
    expect(body).toHaveProperty('scrollTop', 0)
    expect(screen.getAllByRole('button', { name: '查看引用 2 的证据' })[0]).toHaveAttribute(
      'aria-expanded',
      'true',
    )
  })

  it('Escape 关闭证据栏并把焦点还给触发引用', async () => {
    const user = userEvent.setup()
    render(
      <ReportView
        markdown={MARKDOWN}
        streaming={false}
        findings={FINDINGS}
        citations={CITATIONS}
      />,
    )

    const [cite1] = screen.getAllByRole('button', { name: '查看引用 1 的证据' })
    await user.click(cite1)
    await user.keyboard('{Escape}')

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(cite1).toHaveFocus()
  })

  it('旧运行没有上下文时明确降级为已验证摘录', async () => {
    const user = userEvent.setup()
    render(
      <ReportView
        markdown={MARKDOWN}
        streaming={false}
        findings={FINDINGS.map((finding) => ({
          ...finding,
          verification: { ...finding.verification, evidence_context: undefined },
        }))}
        citations={CITATIONS}
      />,
    )

    const [cite1] = screen.getAllByRole('button', { name: '查看引用 1 的证据' })
    await user.click(cite1)

    expect(screen.getByText('旧记录未保存上下文')).toBeInTheDocument()
    expect(screen.getByText('GPU shipments hit a record high in Q4')).toBeInTheDocument()
  })

  it('conflicted 论断在侧栏中渲染矛盾徽章与反向 claim 链接', async () => {
    const user = userEvent.setup()
    render(
      <ReportView
        markdown={MARKDOWN}
        streaming={false}
        findings={FINDINGS}
        citations={CITATIONS}
      />,
    )

    const [cite2] = screen.getAllByRole('button', { name: '查看引用 2 的证据' })
    await user.click(cite2)

    const drawer = within(screen.getByRole('dialog', { name: '引用 2 的证据' }))
    expect(drawer.getByText('conflicted')).toBeInTheDocument()
    expect(drawer.getByText('两来源对功耗趋势结论相反')).toBeInTheDocument()
    // 反向 claim（claim-3）的论断与来源链接
    expect(drawer.getByText('新工艺下整机功耗明显下降')).toBeInTheDocument()
    expect(drawer.getByTitle('https://c.example.com/efficiency')).toHaveAttribute(
      'href',
      'https://c.example.com/efficiency',
    )
  })

  it('概览条分开展示论断、原文匹配、语义支持、冲突与来源拦截数', () => {
    render(
      <ReportView
        markdown={MARKDOWN}
        streaming={false}
        findings={FINDINGS}
        citations={CITATIONS}
        blockedSources={4}
      />,
    )

    expect(screen.getByTestId('evidence-records')).toHaveTextContent('3 证据记录')
    expect(screen.getByTestId('evidence-verbatim')).toHaveTextContent('2 原文匹配')
    expect(screen.getByTestId('evidence-supported')).toHaveTextContent('2 语义支持')
    expect(screen.getByTestId('evidence-conflicted')).toHaveTextContent('1 存在冲突')
    expect(screen.getByTestId('evidence-blocked')).toHaveTextContent('4 来源被拦截')
  })

  it('事件流拿不到审计事件时，概览条降级只显示前三项并注明', () => {
    render(
      <ReportView
        markdown={MARKDOWN}
        streaming={false}
        findings={FINDINGS}
        citations={CITATIONS}
        blockedSources={null}
      />,
    )

    expect(screen.queryByTestId('evidence-blocked')).not.toBeInTheDocument()
    expect(screen.getByText(/拦截数不可用/)).toBeInTheDocument()
  })

  it('流式阶段无 findings 时优雅降级：引用不可点击、不显示概览条', () => {
    render(<ReportView markdown={MARKDOWN} streaming={true} findings={[]} citations={[]} />)

    expect(screen.queryByRole('button', { name: /查看引用/ })).not.toBeInTheDocument()
    expect(screen.queryByTestId('evidence-overview')).not.toBeInTheDocument()
    // [n] 仍以普通角标文本渲染
    expect(screen.getAllByText('[1]').length).toBeGreaterThan(0)
  })
})
