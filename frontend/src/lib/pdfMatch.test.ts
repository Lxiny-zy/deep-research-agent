import { describe, expect, it } from 'vitest'
import { findQuote, normalizeForMatch } from './pdfMatch'

describe('findQuote', () => {
  const pages = [
    ['Introduction', 'Snapshot spectral imaging encodes', 'a scene with a coded aperture.'],
    ['Results', 'The proposed recon-', 'struction reaches 38.4 dB', 'PSNR on CAVE.', 'Table 2'],
  ]

  it('ignores whitespace, case and line-break hyphenation', () => {
    const match = findQuote(pages, 'the proposed reconstruction reaches 38.4 dB PSNR')!
    expect(match.page).toBe(1)
    expect(match.ranges.map((r) => pages[r.page][r.item].slice(r.start, r.end))).toEqual([
      'The proposed recon-',
      'struction reaches 38.4 dB',
      'PSNR',
    ])
  })

  it('rejects a matching prefix when the rest of the quotation is absent', () => {
    const match = findQuote(
      pages,
      'Snapshot spectral imaging encodes a scene with a coded aperture and a disperser in one shot',
    )
    expect(match).toBeNull()
  })

  it('matches CJK text split across text items', () => {
    expect(
      findQuote(
        [['本文提出一种', '深度展开网络，', '用于光谱重建。']],
        '深度展开网络，用于光谱重建',
      ),
    ).toEqual({
      page: 0,
      ranges: [
        { page: 0, item: 1, start: 0, end: 7 },
        { page: 0, item: 2, start: 0, end: 6 },
      ],
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
    'locates a preserved scientific exponent across PDF text spans: %s',
    (quote) => {
      const page = ['Table 1', '3.742', ' ×', ' 10', '6', 'next row']
      const match = findQuote([page], quote)!
      expect(match.page).toBe(0)
      expect(match.ranges.map((r) => page[r.item].slice(r.start, r.end))).toEqual([
        '3.742',
        '×',
        '10',
        '6',
      ])
      expect(normalizeForMatch('x^2 + 10^6')).toBe('x^2+10^6')
    },
  )

  it('returns only the quoted characters inside a line with neighboring sentences', () => {
    const line = 'Previous sentence. Target quote here. Next sentence.'
    const quote = 'Target quote here.'
    expect(findQuote([[line]], quote)).toEqual({
      page: 0,
      ranges: [
        { page: 0, item: 0, start: line.indexOf(quote), end: line.indexOf(quote) + quote.length },
      ],
    })
  })

  it('keeps character offsets correct for ligatures, composed accents and astral text', () => {
    const line = 'Prefix: 😀 ﬁeld cafe\u0301. Suffix'
    const match = findQuote([[line]], '😀 field café.')!
    expect(line.slice(match.ranges[0].start, match.ranges[0].end)).toBe('😀 ﬁeld cafe\u0301.')
  })

  it('preserves real hyphens and supports hyphenation in a backend quotation', () => {
    const page = ['A cross-', 'spectral recon-', 'struction network.']
    expect(findQuote([page], 'cross-spectral reconstruction network.')?.page).toBe(0)
    expect(findQuote([['A reconstruction network.']], 'recon-\nstruction network.')?.page).toBe(0)
  })

  it('returns ranges on both pages for an entire cross-page quotation', () => {
    expect(
      findQuote(
        [['Before. The entire quote'], ['continues here. After.']],
        'The entire quote continues here.',
      ),
    ).toEqual({
      page: 0,
      ranges: [
        { page: 0, item: 0, start: 8, end: 24 },
        { page: 1, item: 0, start: 0, end: 15 },
      ],
    })
  })

  it('does not jump to the first of several identical occurrences', () => {
    expect(
      findQuote([['A repeated quote.'], ['A repeated quote.']], 'A repeated quote.'),
    ).toBeNull()
    expect(findQuote([['A repeated quote. A repeated quote.']], 'A repeated quote.')).toBeNull()
  })

  it('does not match a conclusion just because it shares the quote ending', () => {
    const quote =
      'However, there are no multispectral stereo databases for training disparity estimation networks available.'
    expect(
      findQuote(
        [['Other content'], ['Our approach differs from disparity estimation networks available.']],
        quote,
      ),
    ).toBeNull()
    expect(
      findQuote(
        [
          [quote],
          ['Other content'],
          ['Our approach differs from disparity estimation networks available.'],
        ],
        quote,
      )?.page,
    ).toBe(0)
  })
})
