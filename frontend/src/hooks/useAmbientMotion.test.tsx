import { act, cleanup, renderHook } from '@testing-library/react'
import { useAmbientMotion } from './useAmbientMotion'

describe('ambient motion preferences', () => {
  let media: EventTarget & { matches: boolean }
  let hidden: boolean

  beforeEach(() => {
    localStorage.clear()
    hidden = false
    media = Object.assign(new EventTarget(), { matches: false })
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => media),
    )
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
  })

  afterEach(() => {
    cleanup()
    localStorage.clear()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('remembers a pause after navigating away and keeps active controls in sync', () => {
    const first = renderHook(useAmbientMotion)
    const second = renderHook(useAmbientMotion)
    act(() => first.result.current.toggle())
    expect(second.result.current.paused).toBe(true)
    first.unmount()
    second.unmount()
    const restored = renderHook(useAmbientMotion)
    expect(restored.result.current.paused).toBe(true)
    act(() => restored.result.current.toggle())
    expect(restored.result.current.paused).toBe(false)
  })

  it('responds to system motion changes without losing the user preference', () => {
    const { result } = renderHook(useAmbientMotion)
    act(() => {
      media.matches = true
      media.dispatchEvent(new Event('change'))
    })
    expect(result.current.paused).toBe(true)
    act(() => result.current.toggle())
    expect(result.current.paused).toBe(true)
    act(() => {
      media.matches = false
      media.dispatchEvent(new Event('change'))
    })
    expect(result.current.paused).toBe(false)
  })

  it('suspends a hidden page without turning a temporary suspension into a saved pause', () => {
    const { result } = renderHook(useAmbientMotion)
    act(() => {
      hidden = true
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(result.current.inactive).toBe(true)
    expect(result.current.paused).toBe(false)
    act(() => {
      hidden = false
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(result.current.inactive).toBe(false)
  })

  it('keeps playback usable when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const { result } = renderHook(useAmbientMotion)
    act(() => result.current.toggle())
    expect(result.current.paused).toBe(true)
    act(() => result.current.toggle())
    expect(result.current.paused).toBe(false)
  })
})
