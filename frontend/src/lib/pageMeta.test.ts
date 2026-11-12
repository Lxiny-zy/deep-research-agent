import { PAGE_META, issueLabel, pageKey } from './pageMeta'

describe('page meta', () => {
  it('maps routes to their section', () => {
    expect(pageKey('/')).toBe('home')
    expect(pageKey('/qa')).toBe('qa')
    expect(pageKey('/history')).toBe('history')
    expect(pageKey('/library')).toBe('library')
    expect(pageKey('/runs/abc')).toBe('run')
    expect(pageKey('/runs/abc/read')).toBe('reader')
    expect(pageKey('/settings')).toBe('tools')
    expect(pageKey('/workflows')).toBe('workflows')
    expect(pageKey('/agents')).toBe('agents')
  })

  it('gives every page a unique stage and section number', () => {
    const pages = Object.values(PAGE_META)
    expect(new Set(pages.map((page) => page.stage)).size).toBe(pages.length)
    expect(new Set(pages.map((page) => page.section)).size).toBe(pages.length)
    for (const page of pages) expect(page.section).toMatch(/^\d{2}$/)
  })

  it('formats a journal-style issue label', () => {
    expect(issueLabel(new Date(2026, 10, 12))).toBe('Vol. XI · No. 11')
    expect(issueLabel(new Date(2016, 0, 1))).toBe('Vol. I · No. 1')
  })
})
