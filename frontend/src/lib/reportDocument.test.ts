import { normalizeReportDocument } from './reportDocument'

describe('normalizeReportDocument', () => {
  it('adapts the legacy Report response into a prose document', () => {
    expect(
      normalizeReportDocument({
        query: 'legacy query',
        markdown: '# Legacy report',
        citations: ['https://example.test/paper'],
      }),
    ).toEqual({
      schema_version: 1,
      query: 'legacy query',
      blocks: [{ kind: 'prose', markdown: '# Legacy report' }],
      references: [
        {
          index: 1,
          url: 'https://example.test/paper',
          reference: 'https://example.test/paper',
        },
      ],
      evidence: [],
      overview: {
        records: 0,
        verbatim_matched: 0,
        semantically_supported: 0,
        corroborated: 0,
        conflicted: 0,
        blocked_sources: null,
      },
      disclaimer: '',
    })
  })

  it('fills defaults for an incomplete structured response', () => {
    const document = normalizeReportDocument({
      query: 'partial query',
      blocks: [
        {
          kind: 'table',
          id: 'metrics',
          columns: [{ key: 'psnr', numeric: true }],
          rows: [{ label: 'Method A', cells: { psnr: { value: 38.36, citations: ['1'] } } }],
        },
      ],
      evidence: [
        {
          citation: 1,
          source_url: 'https://example.test/paper',
          quote: 'source quote',
          status: 'verified',
        },
      ],
    })

    expect(document?.blocks).toEqual([
      {
        kind: 'table',
        id: 'metrics',
        title: '',
        columns: [
          {
            key: 'psnr',
            label: 'psnr',
            unit: '',
            align: 'left',
            numeric: true,
            note_ref: null,
          },
        ],
        rows: [
          {
            label: 'Method A',
            citation: null,
            cells: {
              psnr: {
                value: '38.36',
                numeric: null,
                citations: [1],
                note_ref: null,
                disputed: false,
              },
            },
          },
        ],
        notes: [],
        caption: '',
      },
    ])
    expect(document?.evidence[0]).toMatchObject({
      quote: 'source quote',
      context: '',
      content_hash: '',
      verbatim_verified: true,
      quantity_status: 'not_applicable',
      contradicts_claim_ids: [],
    })
    expect(document?.overview).toEqual({
      records: 1,
      verbatim_matched: 1,
      semantically_supported: 0,
      corroborated: 0,
      conflicted: 0,
      blocked_sources: null,
    })
  })

  it('returns null for an unrelated payload so the caller can use its legacy fallback', () => {
    expect(normalizeReportDocument(null)).toBeNull()
    expect(normalizeReportDocument({ ok: true })).toBeNull()
  })
})
