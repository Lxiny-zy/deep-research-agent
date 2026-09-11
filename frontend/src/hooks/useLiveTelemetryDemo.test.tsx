import { act, cleanup, renderHook } from '@testing-library/react'
import { useLiveTelemetryDemo } from './useLiveTelemetryDemo'

describe('welcome telemetry playback', () => {
  beforeEach(() =>
    vi.useFakeTimers({
      toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'performance'],
    }),
  )
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('suspends the demo while off screen and resumes without counting the hidden time', () => {
    const { result, rerender } = renderHook(
      ({ active }) => useLiveTelemetryDemo(true, { active }),
      { initialProps: { active: true } },
    )
    act(() => vi.advanceTimersByTime(1000))
    expect(result.current.elapsed).toBe(1)
    rerender({ active: false })
    act(() => vi.advanceTimersByTime(10000))
    expect(result.current.elapsed).toBe(1)
    expect(vi.getTimerCount()).toBe(0)
    rerender({ active: true })
    act(() => vi.advanceTimersByTime(1000))
    expect(result.current.elapsed).toBe(2)
  })

  it('provides a stable completed preview without starting timers for reduced motion', () => {
    const { result } = renderHook(() => useLiveTelemetryDemo(true, { staticPreview: true }))
    expect(result.current.done).toBe(true)
    expect(result.current.progress.percent).toBe(100)
    expect(vi.getTimerCount()).toBe(0)
  })
})
