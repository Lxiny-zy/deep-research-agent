import {
  lineLabel,
  lineX,
  MAX_LINES,
  padLines,
  PAGE_SPECTRA,
  spectrumKey,
  stepLines,
  wavelengthToRgb,
} from './spectralLines'

describe('spectral line identity', () => {
  it('assigns every area of the app its own element', () => {
    expect(spectrumKey('/')).toBe('home')
    expect(spectrumKey('/qa')).toBe('qa')
    expect(spectrumKey('/history')).toBe('history')
    expect(spectrumKey('/library')).toBe('library')
    expect(spectrumKey('/runs/abc')).toBe('run')
    expect(spectrumKey('/runs/abc/read')).toBe('reader')
    expect(spectrumKey('/settings')).toBe('tools')
    expect(spectrumKey('/workflows')).toBe('workflows')
    expect(spectrumKey('/agents')).toBe('agents')

    const elements = Object.values(PAGE_SPECTRA).map((page) => page.element)
    expect(new Set(elements).size).toBe(elements.length)
    for (const page of Object.values(PAGE_SPECTRA)) {
      expect(page.lines).toContain(page.line)
      expect(page.lines.length).toBeLessThanOrEqual(MAX_LINES)
      for (const line of page.lines) {
        expect(line.nm).toBeGreaterThanOrEqual(380)
        expect(line.nm).toBeLessThanOrEqual(780)
      }
    }
  })

  it('labels a line with its symbol and wavelength', () => {
    expect(lineLabel(PAGE_SPECTRA.home.line)).toBe('Hα 656.3 nm')
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

  it('pads with dark lines and slides toward the next element', () => {
    const current = padLines(PAGE_SPECTRA.home.lines)
    expect(current).toHaveLength(MAX_LINES)
    expect(current.slice(4).every((line) => line.strength === 0)).toBe(true)
    const target = padLines(PAGE_SPECTRA.history.lines)
    let moving = stepLines(current, target, 0.5)
    expect(moving).toBe(true)
    for (let i = 0; i < 80 && moving; i++) moving = stepLines(current, target, 0.5)
    expect(moving).toBe(false)
    expect(current[0].nm).toBeCloseTo(target[0].nm, 1)
  })
})
