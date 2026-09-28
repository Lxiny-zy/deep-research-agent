import { MAX_PEAKS, SIGNATURES, gaussian, padPeaks, signatureKey, stepToward } from './spectrum'

describe('spectrum signatures', () => {
  it('gives each area of the app its own spectrum', () => {
    expect(signatureKey('/')).toBe('home')
    expect(signatureKey('/qa')).toBe('qa')
    expect(signatureKey('/history')).toBe('history')
    expect(signatureKey('/library')).toBe('library')
    expect(signatureKey('/runs/abc')).toBe('run')
    expect(signatureKey('/settings')).toBe('tools')
    expect(signatureKey('/workflows')).toBe('workflows')
    expect(signatureKey('/agents')).toBe('agents')

    const shapes = Object.values(SIGNATURES).map((s) =>
      s.peaks.map((p) => `${p.x}:${p.w}:${p.h}`).join('|'),
    )
    expect(new Set(shapes).size).toBe(shapes.length)
    for (const signature of Object.values(SIGNATURES)) {
      expect(signature.peaks.length).toBeGreaterThan(0)
      expect(signature.peaks.length).toBeLessThanOrEqual(MAX_PEAKS)
    }
  })

  it('pads with zero-height peaks so a page change can grow new peaks', () => {
    const padded = padPeaks(SIGNATURES.home.peaks)
    expect(padded).toHaveLength(MAX_PEAKS)
    expect(padded.slice(3).every((p) => p.h === 0)).toBe(true)
  })

  it('morphs toward the target and settles', () => {
    const current = padPeaks(SIGNATURES.home.peaks).map((p) => ({ ...p, rgb: [...p.rgb] }))
    const target = padPeaks(SIGNATURES.library.peaks)
    expect(stepToward(current as never, target, 0.5)).toBe(true)
    let moving = true
    for (let i = 0; i < 60 && moving; i++) moving = stepToward(current as never, target, 0.5)
    expect(moving).toBe(false)
    expect(current[5].h).toBeCloseTo(target[5].h, 3)
  })

  it('uses a unit gaussian profile', () => {
    expect(gaussian(0.5, 0.5, 0.1)).toBe(1)
    expect(gaussian(0.7, 0.5, 0.1)).toBeLessThan(0.02)
  })
})
