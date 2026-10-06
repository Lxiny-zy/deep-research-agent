import { canLocateReadingAnchor, readingNavigation } from './readingMap'

const anchor = {
  document_id: 'att-paper',
  pdf_available: true,
  quote: 'A complete original quotation.',
  quote_truncated: false,
  quote_redacted: false,
}

it('only permits complete original quotations tied to an available PDF', () => {
  expect(canLocateReadingAnchor(anchor)).toBe(true)
  for (const patch of [
    { document_id: null },
    { pdf_available: false },
    { quote: '' },
    { quote_truncated: true },
    { quote_redacted: true },
  ])
    expect(canLocateReadingAnchor({ ...anchor, ...patch })).toBe(false)
})

it('rejects malformed or unbound navigation state instead of selecting the current PDF', () => {
  const navigation = {
    runId: 'run',
    documentVersion: 'a'.repeat(64),
    includeHsiTables: false,
    unitId: 'unit',
    anchor,
  }
  expect(readingNavigation(navigation)).toEqual(navigation)
  expect(readingNavigation({ ...navigation, documentVersion: 'unknown' })).toBeNull()
  expect(
    readingNavigation({ ...navigation, anchor: { ...anchor, pdf_available: false } }),
  ).toBeNull()
  expect(readingNavigation({ ...navigation, anchor: { document_id: 'att-paper' } })).toBeNull()
  expect(readingNavigation(null)).toBeNull()
})
