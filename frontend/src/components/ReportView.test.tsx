import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Finding, ReportBibliography } from '../types'
import ReportView from './ReportView'

it('distinguishes failed final prose review from verified source evidence', async () => {
  render(
    <ReportView
      markdown="未经支持的结论 [1]。"
      streaming={false}
      finalReview={{ status: 'fail', issues: ['引用只支持相关，不能证明因果'] }}
    />,
  )
  expect(screen.getByRole('alert')).toHaveTextContent('正式交付暂不可用')
  await userEvent.click(screen.getByText('查看未通过的内容'))
  expect(screen.getByText('引用只支持相关，不能证明因果')).toBeVisible()
})

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
  corroboration_status?: 'not_checked' | 'single_source' | 'corroborated' | 'disputed'
  independent_source_count?: number
  corroborates_claim_ids?: string[]
  corroboration_reason?: string
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
      corroboration_status: over.corroboration_status ?? 'single_source',
      independent_source_count: over.independent_source_count ?? 1,
      corroborates_claim_ids: over.corroborates_claim_ids ?? [],
      corroboration_reason: over.corroboration_reason ?? '',
    },
  }
}

it('shows one paper while each inline citation opens its own original locations and complete quote', async () => {
  const urls = [1, 2, 3].map((n) => `https://workspace.invalid/attachments/paper?chunk=${n}`)
  const markdown = '方法 [2]。综合 [1,3]。'
  const bibliography: ReportBibliography = {
    source_body: markdown,
    body: '方法 [[1]](#cite-2)。综合 [[1]](#cite-1-3)。',
    documents: [
      {
        index: 1,
        identity: 'paper',
        title: 'Study',
        reference: 'Author. Study. 2024.',
        url: '',
        locations: [1, 2, 3],
      },
    ],
    locations: urls.map((url, i) => ({
      index: i + 1,
      document: 1,
      url,
      label: `第 ${i + 1} 页`,
      content_hashes: [],
    })),
  }
  const findings = urls.map((url, i) =>
    makeFinding({
      source_url: url,
      statement: `CLAIM_${i + 1}`,
      claim_id: `c${i}`,
      evidence_quote: i === 2 ? 'long evidence '.repeat(100) + 'END FULL QUOTE' : `QUOTE_${i + 1}`,
      evidence_context: i === 2 ? 'long evidence '.repeat(20) : undefined,
    }),
  )
  const { rerender } = render(
    <ReportView
      markdown={markdown}
      citations={urls}
      findings={findings}
      bibliography={bibliography}
      streaming={false}
    />,
  )
  expect(
    within(screen.getByRole('region', { name: '参考来源' })).getAllByRole('listitem'),
  ).toHaveLength(1)
  await userEvent.click(screen.getAllByRole('button', { name: '查看引用 1 的证据' })[0])
  let panel = screen.getByRole('dialog', { name: '引用 1 的证据' })
  expect(within(panel).getByText('未绑定依据：本句没有可用的核验绑定记录。')).toBeVisible()
  expect(within(panel).queryByText('CLAIM_2')).toBeNull()
  await userEvent.click(within(panel).getByRole('button', { name: '查看这些位置的全部记录' }))
  expect(within(panel).getByText('CLAIM_2')).toBeVisible()
  expect(within(panel).queryByText('CLAIM_1')).toBeNull()
  expect(within(panel).getByRole('combobox')).toHaveValue('2')
  await userEvent.click(within(panel).getByRole('button', { name: '关闭证据侧栏' }))
  await userEvent.click(screen.getAllByRole('button', { name: '查看引用 1 的证据' })[1])
  panel = screen.getByRole('dialog', { name: '引用 1 的证据' })
  await userEvent.click(within(panel).getByRole('button', { name: '查看这些位置的全部记录' }))
  expect(within(panel).getByText('CLAIM_1')).toBeVisible()
  expect(within(panel).getByText('CLAIM_3')).toBeVisible()
  expect(within(panel).getByText(/END FULL QUOTE/)).toBeVisible()
  expect(within(panel).queryByText('CLAIM_2')).toBeNull()
  await userEvent.click(within(panel).getByRole('button', { name: '关闭证据侧栏' }))
  rerender(
    <ReportView
      markdown="Changed [2]."
      citations={urls}
      findings={findings}
      bibliography={bibliography}
      streaming={false}
    />,
  )
  expect(screen.getByRole('button', { name: '查看引用 2 的证据' })).toBeVisible()
})

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

it('keeps legacy inline citations unbound even after the reader browses the source', async () => {
  render(
    <ReportView
      markdown="结论 [1]。"
      streaming={false}
      citations={[CITATIONS[0]]}
      findings={[
        makeFinding({
          statement: '来源记录',
          source_url: CITATIONS[0],
          evidence_quote: 'SOURCE_QUOTE',
          claim_id: 'legacy',
        }),
      ]}
    />,
  )
  const citation = screen.getByRole('button', { name: '查看引用 1 的证据' })
  await userEvent.click(citation)
  expect(screen.getByText('未绑定依据：本句没有可用的核验绑定记录。')).toBeVisible()
  expect(screen.queryByText('SOURCE_QUOTE')).toBeNull()
  await userEvent.click(screen.getByRole('button', { name: '查看这些位置的全部记录' }))
  expect(screen.getByText('SOURCE_QUOTE')).toBeVisible()
  expect(screen.getByText('正在浏览来源记录；这些记录不代表本句的核验依据。')).toBeVisible()
  await userEvent.click(citation)
  expect(screen.queryByText('SOURCE_QUOTE')).toBeNull()
  expect(screen.getByText('未绑定依据：本句没有可用的核验绑定记录。')).toBeVisible()
})

it('lets readers interact outside the evidence panel without locking scroll or restoring old focus', async () => {
  const bodyOverflow = document.body.style.overflow
  const rootOverflow = document.documentElement.style.overflow
  document.body.style.overflow = 'auto'
  document.documentElement.style.overflow = 'scroll'
  try {
    const action = vi.fn()
    render(
      <>
        <button onClick={action}>正文旁的操作</button>
        <ReportView
          markdown={MARKDOWN}
          streaming={false}
          findings={FINDINGS}
          citations={CITATIONS}
        />
      </>,
    )
    const user = userEvent.setup()
    const cite = screen.getAllByRole('button', { name: '查看引用 1 的证据' })[0]
    await user.click(cite)
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'false')
    expect(document.querySelector('.evidence-backdrop')).toBeNull()
    expect(document.body.style.overflow).toBe('auto')
    expect(document.documentElement.style.overflow).toBe('scroll')
    const focus = vi.spyOn(cite, 'focus')
    await user.click(screen.getByRole('button', { name: '正文旁的操作' }))
    expect(action).toHaveBeenCalledOnce()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('button', { name: '正文旁的操作' })).toHaveFocus()
    expect(focus).not.toHaveBeenCalled()
    focus.mockRestore()
  } finally {
    document.body.style.overflow = bodyOverflow
    document.documentElement.style.overflow = rootOverflow
  }
})

it('returns keyboard focus on close without scrolling back to the citation', async () => {
  render(
    <ReportView markdown={MARKDOWN} streaming={false} findings={FINDINGS} citations={CITATIONS} />,
  )
  const user = userEvent.setup()
  const cite = screen.getAllByRole('button', { name: '查看引用 1 的证据' })[0]
  await user.click(cite)
  const focus = vi.spyOn(cite, 'focus')
  await user.keyboard('{Escape}')
  expect(focus).toHaveBeenCalledWith({ preventScroll: true })
  expect(cite).toHaveFocus()
  focus.mockRestore()
})

it('shows a readable explanation for failed source checks while preserving raw diagnostic data', async () => {
  const finding = makeFinding({
    statement: '当前结论',
    source_url: CITATIONS[0],
    evidence_quote: 'Quoted evidence.',
    claim_id: 'c1',
    consistency_status: 'not_checked',
    corroboration_status: 'not_checked',
    contradiction_reason: 'consistency_verifier_failed:ValueError',
    corroboration_reason: 'consistency_verifier_failed:ValueError',
  })
  render(
    <ReportView
      markdown="当前结论 [1]。"
      streaming={false}
      findings={[finding]}
      citations={CITATIONS}
    />,
  )
  await userEvent.click(screen.getByRole('button', { name: '查看引用 1 的证据' }))
  const panel = screen.getByRole('dialog')
  await userEvent.click(within(panel).getByRole('button', { name: '查看这些位置的全部记录' }))
  expect(panel).toHaveTextContent('来源之间的一致性核对未完成')
  expect(panel).toHaveTextContent('其他来源的支持情况')
  expect(panel.innerHTML).not.toMatch(/consistency_verifier_failed|ValueError/)
  expect(finding.verification.corroboration_reason).toBe('consistency_verifier_failed:ValueError')
})

it('uses the selected evidence for each occurrence, allows explicit browsing and closes stale selection', async () => {
  const url = 'https://example.org/paper'
  const markdown = 'CLAIM-A [1].\n\nCLAIM-B [1].'
  const ids = ['a'.repeat(24), 'b'.repeat(24)]
  const catalog: ReportBibliography = {
    source_body: markdown,
    body: `CLAIM-A [[1]](#cite-o-${ids[0]}).\n\nCLAIM-B [[1]](#cite-o-${ids[1]}).`,
    documents: [
      { index: 1, identity: 'paper', title: 'Paper', reference: 'Paper', url, locations: [1] },
    ],
    locations: [{ index: 1, document: 1, url, label: '第 2 页', content_hashes: [] }],
    binding_status: 'bound',
    occurrences: ids.map((id, run) => ({
      id,
      run,
      document: 1,
      locations: [1],
      unit_id: `unit-${run}`,
      scope: 'reviewed_unit',
      evidence_ids: [`e-${run}`],
    })),
  }
  const findings = [0, 1, 2].map((index) => ({
    ...makeFinding({
      source_url: url,
      statement: `Record ${index}`,
      evidence_quote: `Quote ${index}`,
      claim_id: `c-${index}`,
    }),
    support_id: `e-${index}`,
  }))
  const { rerender } = render(
    <ReportView
      markdown={markdown}
      citations={[url]}
      findings={findings}
      bibliography={catalog}
      streaming={false}
    />,
  )
  const buttons = screen.getAllByRole('button', { name: '查看引用 1 的证据' })
  await userEvent.click(buttons[0])
  let panel = screen.getByRole('dialog')
  expect(within(panel).getByText('Record 0')).toBeVisible()
  expect(within(panel).queryByText('Record 1')).toBeNull()
  expect(within(panel).queryByText('Record 2')).toBeNull()
  expect(buttons[0]).toHaveAttribute('aria-pressed', 'true')
  expect(buttons[1]).toHaveAttribute('aria-pressed', 'false')
  await userEvent.click(within(panel).getByRole('button', { name: '查看这些位置的全部记录' }))
  expect(within(panel).getByText('Record 2')).toBeVisible()
  await userEvent.click(within(panel).getByRole('button', { name: '关闭证据侧栏' }))
  await userEvent.click(buttons[1])
  panel = screen.getByRole('dialog')
  expect(within(panel).getByText('Record 1')).toBeVisible()
  expect(within(panel).queryByText('Record 0')).toBeNull()
  rerender(
    <ReportView
      markdown={markdown}
      citations={[url]}
      findings={findings}
      bibliography={{
        ...catalog,
        binding_status: 'invalid',
        occurrences: [],
        body: 'CLAIM-A [1].\n\nCLAIM-B [1].',
      }}
      streaming={false}
    />,
  )
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('does not replace a missing selected quote or an unused location with other records', async () => {
  const url = 'https://example.org/paper'
  const id = 'c'.repeat(24)
  const markdown = 'Claim [1].'
  const catalog: ReportBibliography = {
    source_body: markdown,
    body: `Claim [[1]](#cite-o-${id}).`,
    binding_status: 'bound',
    documents: [{ index: 1, identity: 'p', title: 'p', reference: 'p', url, locations: [1] }],
    locations: [{ index: 1, document: 1, url, label: '第 1 页', content_hashes: [] }],
    occurrences: [
      {
        id,
        run: 0,
        document: 1,
        locations: [1],
        unit_id: 'p',
        scope: 'reviewed_unit',
        evidence_ids: ['missing'],
      },
    ],
  }
  const findings = [
    {
      ...makeFinding({
        source_url: url,
        statement: 'Unselected record',
        evidence_quote: 'quote',
        claim_id: 'x',
      }),
      support_id: 'other',
    },
  ]
  const { rerender } = render(
    <ReportView
      markdown={markdown}
      citations={[url]}
      findings={findings}
      bibliography={catalog}
      streaming={false}
    />,
  )
  await userEvent.click(screen.getByRole('button', { name: '查看引用 1 的证据' }))
  expect(screen.getByText('该段核验选用的摘录暂未加载。')).toBeVisible()
  expect(screen.queryByText('Unselected record')).toBeNull()
  await userEvent.click(screen.getByRole('button', { name: '关闭证据侧栏' }))
  rerender(
    <ReportView
      markdown={markdown}
      citations={[url]}
      findings={findings}
      bibliography={{
        ...catalog,
        occurrences: [{ ...catalog.occurrences![0], scope: 'unused_location', evidence_ids: [] }],
      }}
      streaming={false}
    />,
  )
  await userEvent.click(screen.getByRole('button', { name: '查看引用 1 的证据' }))
  expect(screen.getByText('这个位置未被本次内容核验选用。')).toBeVisible()
  expect(screen.queryByText('Unselected record')).toBeNull()
})

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
    corroboration_status: 'corroborated',
    independent_source_count: 2,
    corroborates_claim_ids: ['claim-4'],
    corroboration_reason: '两家独立研究机构均报告 Q4 出货量创新高',
  }),
  makeFinding({
    statement: '整机功耗持续上升',
    source_url: 'https://b.example.com/power',
    evidence_quote: 'total system power draw keeps climbing',
    claim_id: 'claim-2',
    consistency_status: 'conflicted',
    contradicts_claim_ids: ['claim-3'],
    contradiction_reason: '两来源对功耗趋势结论相反',
    corroboration_status: 'disputed',
    independent_source_count: 1,
    corroborates_claim_ids: [],
    corroboration_reason: '两来源对功耗趋势结论相反',
  }),
  makeFinding({
    statement: '新工艺下整机功耗明显下降',
    source_url: 'https://c.example.com/efficiency',
    evidence_quote: 'power consumption dropped notably',
    claim_id: 'claim-3',
    status: 'unverified',
  }),
  makeFinding({
    statement: '另一家研究机构确认 Q4 GPU 出货量达到纪录水平',
    source_url: 'https://d.example.org/gpu-market',
    evidence_quote: 'Q4 GPU shipments reached record levels',
    claim_id: 'claim-4',
    source_title: 'Independent GPU Tracker',
    corroboration_status: 'corroborated',
    independent_source_count: 2,
    corroborates_claim_ids: ['claim-1'],
    corroboration_reason: '与 GPU Market Quarterly 的统计结论一致',
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
    await user.click(drawer.getByRole('button', { name: '查看这些位置的全部记录' }))
    expect(drawer.getByText(/After several flat quarters/)).toBeInTheDocument()
    expect(drawer.getByText('GPU shipments hit a record high in Q4')).toHaveProperty(
      'tagName',
      'MARK',
    )
    expect(drawer.getByText('GPU 出货量创下历史新高')).toBeInTheDocument()
    expect(drawer.getByText('GPU Market Quarterly')).toBeInTheDocument()
    expect(drawer.getByText('原文匹配')).toBeInTheDocument()
    expect(drawer.getByText('语义支持 · 模型判定')).toBeInTheDocument()
    expect(drawer.getByText('已交叉印证 · 2 个独立来源')).toBeInTheDocument()
    expect(drawer.getByText('未检测到冲突')).toBeInTheDocument()
    expect(drawer.getByText(/hash abcdef1234/)).toBeInTheDocument()
    expect(drawer.getByText('两家独立研究机构均报告 Q4 出货量创新高')).toBeInTheDocument()
    expect(drawer.getByText('另一家研究机构确认 Q4 GPU 出货量达到纪录水平')).toBeInTheDocument()
    expect(
      drawer.getByRole('link', { name: /打开佐证来源：Independent GPU Tracker/ }),
    ).toHaveAttribute('href', 'https://d.example.org/gpu-market')
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
    await user.click(screen.getByRole('button', { name: '查看这些位置的全部记录' }))
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

    await user.click(screen.getByRole('button', { name: '查看这些位置的全部记录' }))
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
    await user.click(drawer.getByRole('button', { name: '查看这些位置的全部记录' }))
    expect(drawer.getByText('conflicted')).toBeInTheDocument()
    expect(drawer.getByText('来源存在争议 · 1 个独立来源')).toBeInTheDocument()
    expect(drawer.getByText('两来源对功耗趋势结论相反')).toBeInTheDocument()
    // 反向 claim（claim-3）的论断与来源链接
    expect(drawer.getByText('新工艺下整机功耗明显下降')).toBeInTheDocument()
    expect(drawer.getByTitle('https://c.example.com/efficiency')).toHaveAttribute(
      'href',
      'https://c.example.com/efficiency',
    )
  })

  it('概览条分开展示论断、原文匹配、语义支持、交叉印证、冲突与来源拦截数', () => {
    render(
      <ReportView
        markdown={MARKDOWN}
        streaming={false}
        findings={FINDINGS}
        citations={CITATIONS}
        blockedSources={4}
      />,
    )

    expect(screen.getByTestId('evidence-records')).toHaveTextContent('4 证据记录')
    expect(screen.getByTestId('evidence-verbatim')).toHaveTextContent('3 原文匹配')
    expect(screen.getByTestId('evidence-supported')).toHaveTextContent('3 语义支持')
    expect(screen.getByTestId('evidence-corroborated')).toHaveTextContent('2 已交叉印证')
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
    // 流式阶段直接显示正文，避免每个增量重新解析整份 Markdown。
    expect(screen.getByText(/GPU 出货量创新高/)).toHaveTextContent('[1]')
  })
})

// 结构化文档把「## 参考来源」从正文剥进独立字段（report/assemble.py 的 _body），
// 所以屏幕上的来源清单必须由本组件补渲染，否则 run 一完成、/document 一响应，
// 读者就只剩下角标、没有可平铺核对的来源列表。
describe('ReportView 参考来源', () => {
  const BODY_ONLY = '# 结论\n\nGPU 出货量创新高 [1]，但整机功耗持续上升 [2]。'

  it('正文已被剥离参考来源段时，仍按 citations 渲染来源清单', () => {
    render(
      <ReportView
        markdown={BODY_ONLY}
        streaming={false}
        findings={FINDINGS}
        citations={CITATIONS}
      />,
    )

    const section = screen.getByRole('region', { name: '参考来源' })
    expect(within(section).getByRole('link', { name: /a\.example\.com\/report/ })).toHaveAttribute(
      'href',
      'https://a.example.com/report',
    )
    expect(within(section).getAllByRole('listitem')).toHaveLength(2)
  })

  it('正文自带参考来源段时不渲染两遍', () => {
    render(
      <ReportView
        markdown={MARKDOWN}
        streaming={false}
        findings={FINDINGS}
        citations={CITATIONS}
      />,
    )

    // MARKDOWN 尾部本就有一段「## 参考来源」；剥离后只应剩独立成节的那一个
    expect(screen.getAllByText('参考来源')).toHaveLength(1)
  })

  it('流式阶段不渲染来源清单——此时的 citations 是会变的残缺快照', () => {
    render(
      <ReportView
        markdown={BODY_ONLY}
        streaming={true}
        findings={FINDINGS}
        citations={CITATIONS}
      />,
    )

    expect(screen.queryByRole('region', { name: '参考来源' })).not.toBeInTheDocument()
  })
})
