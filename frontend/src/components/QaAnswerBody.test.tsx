import { fireEvent, render, screen } from '@testing-library/react'
import QaAnswerBody from './QaAnswerBody'
import type { QaEvidence, ReportBibliography } from '../types'

const first: QaEvidence = {
  source_url: 'https://workspace.invalid/attachments/paper',
  origin: 'paper',
  statement: '选择参考波段',
  evidence_quote: 'reference band selection',
}
const second: QaEvidence = {
  ...first,
  statement: '几何校正与匹配',
  evidence_quote: 'geometric matching',
}

it('uses reviewed evidence instead of lexical similarity and lets the reader choose multiple matches', () => {
  const id = 'a'.repeat(24)
  const text = '选择参考波段 [1]。'
  const binding: ReportBibliography = {
    source_body: text,
    body: `选择参考波段 [[1]](#cite-o-${id})。`,
    binding_status: 'bound',
    documents: [{ index: 1, identity: 'p', title: '', reference: '', url: '', locations: [1] }],
    locations: [{ index: 1, document: 1, url: first.source_url, label: '', content_hashes: [] }],
    occurrences: [
      {
        id,
        run: 0,
        document: 1,
        locations: [1],
        unit_id: 'unit',
        scope: 'reviewed_unit',
        evidence_ids: ['second'],
      },
    ],
  }
  const evidence = [
    { ...first, support_id: 'first' },
    { ...second, support_id: 'second' },
  ]
  const locate = vi.fn()
  const { rerender } = render(
    <QaAnswerBody
      text={text}
      citations={[first.source_url]}
      evidence={evidence}
      binding={binding}
      onLocate={locate}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: '定位引用 1 的论文依据' }))
  expect(locate).toHaveBeenLastCalledWith(evidence[1])
  locate.mockClear()
  rerender(
    <QaAnswerBody
      text={text}
      citations={[first.source_url]}
      evidence={evidence}
      binding={{
        ...binding,
        occurrences: [{ ...binding.occurrences![0], evidence_ids: ['first', 'second'] }],
      }}
      onLocate={locate}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: '定位引用 1 的论文依据' }))
  expect(locate).not.toHaveBeenCalled()
  expect(screen.getByRole('dialog', { name: '选择论文依据' })).toBeVisible()
  fireEvent.click(screen.getAllByRole('button', { name: '定位这条依据' })[1])
  expect(locate).toHaveBeenCalledWith(evidence[1])
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('does not use an unrelated quote when a reviewed selection is unavailable', () => {
  const id = 'b'.repeat(24)
  const text = '回答 [1]。'
  const binding: ReportBibliography = {
    source_body: text,
    body: `回答 [[1]](#cite-o-${id})。`,
    binding_status: 'bound',
    documents: [{ index: 1, identity: 'p', title: '', reference: '', url: '', locations: [1] }],
    locations: [{ index: 1, document: 1, url: first.source_url, label: '', content_hashes: [] }],
    occurrences: [
      {
        id,
        run: 0,
        document: 1,
        locations: [1],
        unit_id: 'unit',
        scope: 'reviewed_unit',
        evidence_ids: ['missing'],
      },
    ],
  }
  render(
    <QaAnswerBody
      text={text}
      citations={[first.source_url]}
      evidence={[first]}
      binding={binding}
      onLocate={vi.fn()}
    />,
  )
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.getByText('[1]')).toHaveAttribute('title', '本次核验选用的摘录暂未加载')
})

it('requires explicit source browsing instead of guessing unbound evidence from paragraph words', () => {
  const locate = vi.fn()
  render(
    <QaAnswerBody
      text={'**直接回答**\n\n选择参考波段 [1]。\n\n几何校正与匹配 [1]。'}
      citations={[first.source_url]}
      evidence={[first, second]}
      onLocate={locate}
    />,
  )
  expect(screen.getByRole('heading', { name: '直接回答' })).toBeInTheDocument()
  fireEvent.click(screen.getAllByRole('button', { name: '浏览引用 1 的来源记录' })[1])
  expect(locate).not.toHaveBeenCalled()
  expect(screen.getByText('未绑定依据：以下是该来源的记录，不代表本句的核验依据。')).toBeVisible()
  fireEvent.click(screen.getAllByRole('button', { name: '定位这条记录' })[1])
  expect(locate).toHaveBeenCalledWith(second)
})

it('expands grouped markers while leaving code markers and unknown references inactive', () => {
  render(
    <QaAnswerBody
      text={'引用 [1, 2]，未知 [9]，代码 `[1]`。'}
      citations={[first.source_url, 'https://example.org/paper']}
      evidence={[first]}
      onLocate={vi.fn()}
    />,
  )
  expect(screen.getByRole('button', { name: '浏览引用 1 的来源记录' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /查看引用 2/ })).toHaveAttribute(
    'href',
    'https://example.org/paper',
  )
  expect(screen.getAllByRole('button')).toHaveLength(1)
  expect(screen.getByText('[9]')).toHaveClass('is-unavailable')
})

it.each([
  'RGB 缩放到 [0,1] [1,2]。',
  'RGB images are rescaled to [0,1] [1,2].',
  'RGB 缩放到 **[0,1]** [1,2]。',
  '**RGB 缩放到** [0,1] [1,2]。',
])(
  'keeps numeric intervals as data while genuine adjacent citations remain clickable: %s',
  (text) => {
    const { container } = render(
      <QaAnswerBody
        text={text}
        citations={[first.source_url, 'https://example.org/second']}
        evidence={[first]}
        onLocate={vi.fn()}
      />,
    )
    expect(container).toHaveTextContent('[0,1]')
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(screen.getByRole('button', { name: '浏览引用 1 的来源记录' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /查看引用 2/ })).toBeInTheDocument()
    expect(screen.queryByText('[0]')).not.toBeInTheDocument()
  },
)

it.each([first.source_url, 'local-paper.pdf'])(
  'labels an unbound local citation without evidence instead of suggesting a location: %s',
  (url) => {
    const locate = vi.fn()
    render(<QaAnswerBody text="回答 [1]。" citations={[url]} onLocate={locate} />)
    const citation = screen.getByText('[1]')
    expect(citation).toHaveAttribute('title', `${url} · 未绑定依据`)
    expect(citation).toHaveAttribute('aria-label', '引用 1：未绑定依据')
    expect(citation).toHaveClass('is-unavailable')
    fireEvent.click(citation)
    expect(locate).not.toHaveBeenCalled()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByRole('link')).toBeNull()
  },
)

it('does not make provisional streaming citations clickable', () => {
  render(
    <QaAnswerBody
      text="尚在生成 [1]。"
      citations={[first.source_url]}
      evidence={[first]}
      onLocate={vi.fn()}
      streaming
    />,
  )
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
  expect(screen.queryByRole('link')).not.toBeInTheDocument()
})
