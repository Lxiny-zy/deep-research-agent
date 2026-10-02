import { describe, expect, it } from 'vitest'
import { findQuote, normalizeForMatch } from './pdfMatch'

describe('findQuote', () => {
  const pages = [
    ['Introduction', 'Snapshot spectral imaging encodes', 'a scene with a coded aperture.'],
    ['Results', 'The proposed recon-', 'struction reaches 38.4 dB', 'PSNR on CAVE.', 'Table 2'],
  ]

  it('ignores whitespace, case and line-break hyphenation', () => {
    expect(findQuote(pages, 'the proposed reconstruction reaches 38.4 dB PSNR')).toEqual({
      page: 1,
      items: [1, 2, 3],
    })
  })

  it('falls back to the opening words when the quote differs later on', () => {
    const match = findQuote(
      pages,
      'Snapshot spectral imaging encodes a scene with a coded aperture and a disperser in one shot',
    )
    expect(match).toEqual({ page: 0, items: [1] })
  })

  it('matches CJK text split across text items', () => {
    expect(
      findQuote(
        [['本文提出一种', '深度展开网络，', '用于光谱重建。']],
        '深度展开网络，用于光谱重建',
      ),
    ).toEqual({
      page: 0,
      items: [1, 2],
    })
  })

  it('returns null when the quote is absent or empty', () => {
    expect(findQuote(pages, 'hyperspectral unmixing with sparse priors')).toBeNull()
    expect(findQuote(pages, '   ')).toBeNull()
  })

  it('normalizes full-width characters', () => {
    expect(normalizeForMatch('ＰＳＮＲ 38.4 dB')).toBe('psnr38.4db')
  })

  it.each(['3.742 × 10^6', '3.742 × 10^{6}', '3.742 × 10⁶'])(
    'locates a preserved scientific exponent across PDF text spans: %s', (quote) => {
      expect(findQuote([['Table 1', '3.742', ' ×', ' 10', '6', 'next row']], quote)).toEqual({
        page: 0, items: [1, 2, 3, 4],
      })
      expect(normalizeForMatch('x^2 + 10^6')).toBe('x^2+10^6')
    },
  )
})
