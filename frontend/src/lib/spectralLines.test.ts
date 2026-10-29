import {
  lineX,
  PAGE_SPECTRA,
  pageLines,
  spectrumKey,
  stepLines,
  wavelengthToRgb,
} from './spectralLines'

describe('spectral line identity', () => {
  it('gives every area of the app its own step in the research flow', () => {
    expect(spectrumKey('/')).toBe('home')
    expect(spectrumKey('/qa')).toBe('qa')
    expect(spectrumKey('/history')).toBe('history')
    expect(spectrumKey('/library')).toBe('library')
    expect(spectrumKey('/runs/abc')).toBe('run')
    expect(spectrumKey('/runs/abc/read')).toBe('reader')
    expect(spectrumKey('/settings')).toBe('tools')
    expect(spectrumKey('/workflows')).toBe('workflows')
    expect(spectrumKey('/agents')).toBe('agents')

    const stages = Object.values(PAGE_SPECTRA).map((page) => page.stage)
    expect(new Set(stages).size).toBe(stages.length)
    for (const page of Object.values(PAGE_SPECTRA)) {
      expect(page.nm).toBeGreaterThanOrEqual(400)
      expect(page.nm).toBeLessThanOrEqual(700)
    }
  })

  it('orders the sidebar from violet to red, one continuous spectrum', () => {
    const nav = ['home', 'qa', 'history', 'library', 'workflows', 'agents', 'tools'] as const
    const positions = nav.map((key) => PAGE_SPECTRA[key].nm)
    expect([...positions].sort((a, b) => a - b)).toEqual(positions)
  })

  it('maps wavelength to a plausible colour and position', () => {
    const [r, , b] = wavelengthToRgb(656.3)
    expect(r).toBeGreaterThan(200)
    expect(b).toBeLessThan(80)
    const [, g] = wavelengthToRgb(527)
    expect(g).toBeGreaterThan(200)
    const [vr, , vb] = wavelengthToRgb(410)
    expect(vb).toBeGreaterThan(vr)
    expect(lineX(380)).toBe(0)
    expect(lineX(780)).toBe(1)
  })

  it('slides the page line toward the next step', () => {
    const current = pageLines('home')
    const target = pageLines('history')
    let moving = stepLines(current, target, 0.5)
    expect(moving).toBe(true)
    for (let i = 0; i < 80 && moving; i++) moving = stepLines(current, target, 0.5)
    expect(moving).toBe(false)
    expect(current[0].nm).toBeCloseTo(target[0].nm, 1)
  })
})
