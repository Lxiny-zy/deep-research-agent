import { act, fireEvent, render, screen } from '@testing-library/react'
import WelcomePage from './WelcomePage'

// jsdom 没有 matchMedia：按需挂一个只回答「减少动态效果」的桩
function mockReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    'matchMedia',
    (query: string) =>
      ({
        matches: reduce && query.includes('reduce'),
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }) as unknown as MediaQueryList,
  )
}

describe('WelcomePage', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('plays the ring transition before entering', () => {
    vi.useFakeTimers()
    mockReducedMotion(false)
    const onEnter = vi.fn()
    render(<WelcomePage onEnter={onEnter} />)

    fireEvent.click(screen.getByRole('button', { name: /进入/ }))
    expect(screen.getByTestId('welcome')).toHaveClass('is-leaving')
    // 过渡期间再次点击不会重复进入
    fireEvent.click(screen.getByRole('button', { name: /进入/ }))
    expect(onEnter).not.toHaveBeenCalled()

    act(() => {
      vi.advanceTimersByTime(1200)
    })
    expect(onEnter).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('welcome')).not.toHaveClass('is-leaving')
  })

  it('enters immediately when reduced motion is requested', () => {
    mockReducedMotion(true)
    const onEnter = vi.fn()
    render(<WelcomePage onEnter={onEnter} />)
    fireEvent.click(screen.getByRole('button', { name: /进入/ }))
    expect(onEnter).toHaveBeenCalledTimes(1)
  })

  it('toggles the theme from the button centre', () => {
    mockReducedMotion(false)
    const onToggleTheme = vi.fn()
    render(<WelcomePage onEnter={() => {}} onToggleTheme={onToggleTheme} dark />)
    fireEvent.click(screen.getByRole('button', { name: '切换到浅色主题' }))
    expect(onToggleTheme).toHaveBeenCalledWith({ x: expect.any(Number), y: expect.any(Number) })
  })
})
