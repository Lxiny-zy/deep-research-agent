import { fireEvent, render, screen } from '@testing-library/react'
import ReadingFulltextPassages from './ReadingFulltextPassages'

it('does not invent quotations for an absence-confirmed record', () => {
  const { container } = render(
    <ReadingFulltextPassages
      status="absence_confirmed"
      passages={[]}
      total={0}
      offset={0}
      onPage={() => {}}
    />,
  )
  expect(screen.getByText('已完成未见信息的范围核查')).toBeInTheDocument()
  expect(container.querySelector('blockquote')).toBeNull()
  expect(screen.queryByRole('button', { name: '定位回查片段' })).not.toBeInTheDocument()
})

it('keeps counterexamples separate and excludes redacted snippets from precise location', () => {
  const locate = vi.fn()
  const passage = {
    source: 'https://paper.example',
    source_hash: 'hash',
    locator: 'page 2',
    start: 1,
    end: 10,
    verdict: 'refutes',
    review_status: 'refuted',
    document_id: 'paper',
    pdf_available: true,
    quote: 'Counterexample quotation',
    quote_truncated: false,
    quote_redacted: false,
  }
  render(
    <ReadingFulltextPassages
      status="refuted"
      passages={[passage, { ...passage, start: 20, end: 30, quote_redacted: true }]}
      total={2}
      offset={0}
      onPage={() => {}}
      onLocate={locate}
    />,
  )
  expect(screen.getByText('回查反例 1')).toBeInTheDocument()
  expect(screen.getAllByRole('button', { name: '定位回查片段' })).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: '定位回查片段' }))
  expect(locate).toHaveBeenCalledWith(passage)
  expect(locate.mock.calls[0][0]).not.toHaveProperty('evidence_id')
})
