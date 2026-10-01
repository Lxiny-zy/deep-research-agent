import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import QaAnswerBody from './QaAnswerBody'
import ReportView from './ReportView'
import { normalizeMathMarkdown } from '../lib/scientificMath'

it('renders fractions and scripts in answers while preserving inline citations', () => {
  const { container } = render(
    <QaAnswerBody
      text={String.raw`模型 $x_i^2+\frac{a}{b}$ [1]。`}
      citations={['https://example.org/paper']}
      evidence={[]}
    />,
  )
  expect(container.querySelector('.katex .mfrac')).not.toBeNull()
  expect(container.querySelector('math')).not.toBeNull()
  expect(screen.getByRole('link')).toHaveAttribute('href', 'https://example.org/paper')
})

it('supports bracket display math, matrices and math code fences in reports', () => {
  const text =
    String.raw`\[\begin{bmatrix}a&b\\c&d\end{bmatrix}\]` + '\n\n```math\n\\frac{1}{N}\n```'
  const { container } = render(<ReportView markdown={text} streaming={false} />)
  expect(container.querySelectorAll('.katex')).toHaveLength(2)
  expect(container.querySelector('mtable')).not.toBeNull()
})

it('keeps prices and code snippets literal', () => {
  const { container } = render(
    <QaAnswerBody
      text={'Cost $5 and $10. Code `$x_i$`. 公式 $y_i$。'}
      citations={[]}
      evidence={[]}
    />,
  )
  expect(container.querySelectorAll('.katex')).toHaveLength(1)
  expect(container).toHaveTextContent('Cost $5 and $10.')
  expect(container.querySelector('code')).toHaveTextContent('$x_i$')
  expect(normalizeMathMarkdown('`\\(x\\)`')).toBe('`\\(x\\)`')
})

it('does not interpret formula indices as bibliography citations or permit trusted HTML', () => {
  const { container } = render(
    <QaAnswerBody
      text={String.raw`$x=[1,2]$ $\href{https://evil.example}{x}$`}
      citations={['https://example.org/a', 'https://example.org/b']}
      evidence={[]}
    />,
  )
  expect(container.querySelectorAll('a')).toHaveLength(0)
})

it('finishes streamed math and enables only the real citation when the answer is complete', () => {
  const props = { citations: ['https://example.org/paper'], evidence: [] }
  const { container, rerender } = render(
    <QaAnswerBody {...props} text={String.raw`由 $x=\frac{`} streaming />,
  )
  rerender(
    <QaAnswerBody {...props} text={String.raw`由 $x=\frac{a}{b}$ 得 $v=[1,2]$ [1]。`} streaming />,
  )
  expect(container.querySelectorAll('.katex')).toHaveLength(2)
  expect(container.querySelectorAll('a')).toHaveLength(0)
  rerender(<QaAnswerBody {...props} text={String.raw`由 $x=\frac{a}{b}$ 得 $v=[1,2]$ [1]。`} />)
  expect(container.querySelectorAll('.katex')).toHaveLength(2)
  expect(screen.getByRole('link')).toHaveAttribute('href', 'https://example.org/paper')
})
