import { regionBounds, regionFigureKey } from './pdfRegions'

it('uses normalized visible-page coordinates and rejects invalid rectangles', () => {
  expect(regionBounds(['10', '20', '75', '90'])).toEqual([0.1, 0.2, 0.75, 0.9])
  for (const invalid of [
    ['', '0', '100', '100'],
    ['0', '0', 'Infinity', '1'],
    ['50', '20', '10', '70'],
    ['0', '-1', '90', '80'],
    ['0', '0', '101', '100'],
  ])
    expect(regionBounds(invalid)).toBeNull()
})

it('normalizes equivalent figure labels for duplicate detection', () => {
  expect(regionFigureKey('Fig. 1a')).toBe('1a')
  expect(regionFigureKey('Figure 1A')).toBe('1a')
  expect(regionFigureKey('图1a')).toBe('1a')
  expect(regionFigureKey('Table 1')).toBeNull()
  expect(regionFigureKey('Fig. 0')).toBeNull()
})
