import { render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { useRevealOnScroll } from './useRevealOnScroll'

function Harness() {
  const ref = useRef<HTMLDivElement>(null)
  useRevealOnScroll(ref)
  return (
    <div ref={ref}>
      <section data-reveal="1" data-testid="a">A</section>
      <section data-reveal="2" data-testid="b">B</section>
    </div>
  )
}

describe('useRevealOnScroll', () => {
  it('falls back to revealing immediately when IntersectionObserver is unavailable (jsdom)', () => {
    // jsdom 没有 IntersectionObserver：hook 必须直接标记 is-revealed，内容不可被藏住。
    expect(typeof globalThis.IntersectionObserver).toBe('undefined')
    render(<Harness />)
    expect(screen.getByTestId('a')).toHaveClass('is-revealed')
    expect(screen.getByTestId('b')).toHaveClass('is-revealed')
  })
})
