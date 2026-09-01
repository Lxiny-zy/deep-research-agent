import type { Finding, ReportEvidence, ResearchEvent } from '../types'
import {
  citedSources,
  countBlockedSources,
  referenceTextFor,
  reportEvidenceToFinding,
  reportEvidenceToFindings,
  resolveCitationTargets,
  stripTrailingReferences,
  summarizeEvidence,
} from './evidence'

function ev(over: Partial<ResearchEvent>): ResearchEvent {
  return { stage: 'RESEARCHER', type: 'info', message: '', elapsed: 1, ...over }
}

describe('countBlockedSources：解析事件流中的 source policy 审计事件', () => {
  it('累加多条 source_policy 审计事件的 blocked 数', () => {
    const events: ResearchEvent[] = [
      ev({ message: '来源策略门禁', data: { category: 'source_policy', allowed: 3, blocked: 2 } }),
      ev({ stage: 'PLANNER', message: '规划', data: null }),
      ev({ message: '来源策略门禁', data: { category: 'source_policy', allowed: 5, blocked: 1 } }),
    ]
    expect(countBlockedSources(events)).toBe(3)
  })

  it('事件流（如历史回放）拿不到审计事件时返回 null，概览条据此降级', () => {
    expect(countBlockedSources([])).toBeNull()
    expect(countBlockedSources([ev({ data: { category: 'other' } })])).toBeNull()
  })
})

describe('reportEvidenceToFinding structured evidence adapter', () => {
  const record: ReportEvidence = {
    citation: 2,
    claim_id: 'claim-document-1',
    statement: 'structured claim',
    quote: 'verbatim quote',
    context: 'before verbatim quote after',
    source_url: 'https://example.test/paper',
    reference: 'Author. Paper. Journal, 2025. https://doi.org/10/example',
    source_section: 'Results',
    content_hash: 'abc123',
    verbatim_verified: true,
    verification_reason: 'normalized quote matched',
    semantic_status: 'supported',
    semantic_confidence: 0.9,
    semantic_reason: 'claim follows source',
    consistency_status: 'conflicted',
    contradicts_claim_ids: ['claim-document-2'],
    contradiction_reason: 'opposing result',
    corroboration_status: 'corroborated',
    independent_source_count: 2,
    corroboration_reason: 'independent source agrees',
    quantity_label: 'PSNR = 38.36 dB',
    conditions_label: 'KAIST',
    quantity_status: 'verified',
    quantity_reason: '',
  }

  it('maps persisted fields into the interactive finding shape', () => {
    const finding = reportEvidenceToFinding(record)
    expect(finding).toMatchObject({
      statement: 'structured claim',
      source_url: 'https://example.test/paper',
      evidence_quote: 'verbatim quote',
      confidence: 0.9,
      verification: {
        status: 'verified',
        method: 'normalized_quote',
        source_content_hash: 'abc123',
        source_reference: record.reference,
        evidence_context: record.context,
        quantity_label: record.quantity_label,
        conditions_label: record.conditions_label,
        quantity_status: record.quantity_status,
        semantic_status: 'supported',
        consistency_status: 'conflicted',
        contradicts_claim_ids: ['claim-document-2'],
        corroboration_status: 'corroborated',
        independent_source_count: 2,
      },
    })
    expect(finding.verification.corroborates_claim_ids).toEqual([])
  })

  it('normalizes unknown statuses and malformed numeric fields', () => {
    const finding = reportEvidenceToFinding({
      ...record,
      verbatim_verified: false,
      semantic_status: 'future_status',
      consistency_status: 'future_status',
      corroboration_status: 'future_status',
      semantic_confidence: 4,
      independent_source_count: -3,
    })
    expect(finding.verification.status).toBe('unverified')
    expect(finding.verification.method).toBe('none')
    expect(finding.confidence).toBe(1)
    expect(finding.verification.semantic_status).toBe('not_checked')
    expect(finding.verification.consistency_status).toBe('not_checked')
    expect(finding.verification.corroboration_status).toBe('not_checked')
    expect(finding.verification.independent_source_count).toBe(0)
  })

  it('adapts the complete record list in order', () => {
    expect(
      reportEvidenceToFindings([record, { ...record, claim_id: 'claim-document-2' }]),
    ).toHaveLength(2)
  })
})

describe('resolveCitationTargets：引用序号到来源 URL 的映射', () => {
  it('优先使用已落库的 report.citations', () => {
    const targets = resolveCitationTargets('无关正文', ['https://a.example.com'])
    expect(targets[0]).toBe('https://a.example.com')
  })

  it('citations 缺失时从「## 参考来源」列表回退解析', () => {
    const markdown =
      '正文 [2]\n\n## 参考来源\n[1] https://a.example.com\n[2] https://b.example.com\n'
    const targets = resolveCitationTargets(markdown, [])
    expect(targets[0]).toBe('https://a.example.com')
    expect(targets[1]).toBe('https://b.example.com')
  })

  it('学术引用行里 URL 在行尾，前面的作者/期刊文本不影响解析', () => {
    const markdown =
      '正文 [1]\n\n## 参考来源\n' +
      '[1] A Wagadarikar, D Brady. Snapshot imaging. Optics Express, 2024. https://doi.org/10.1364/oe.456\n' +
      '[2] Yuanhao Cai 等. MST. CVPR 2022, 2022. doi:10.1109/CVPR.01698. https://arxiv.org/abs/2205.10102v2\n'
    const targets = resolveCitationTargets(markdown, [])
    expect(targets[0]).toBe('https://doi.org/10.1364/oe.456')
    expect(targets[1]).toBe('https://arxiv.org/abs/2205.10102v2')
  })

  it('容忍撤稿/预印本标记出现在 URL 之前', () => {
    const markdown =
      '## 参考来源\n[1] 某作者. 某标题. 某刊, 2020. 【已撤稿】. https://doi.org/10.1/x\n'
    expect(resolveCitationTargets(markdown, [])[0]).toBe('https://doi.org/10.1/x')
  })
})

describe('summarizeEvidence：区分程序匹配与模型语义判断', () => {
  it('不会把语义不支持的原文匹配计为语义支持', () => {
    const base: Finding = {
      statement: 'claim',
      source_url: 'https://a.example.com',
      evidence_quote: 'quote',
      confidence: 0.9,
      verification: {
        status: 'verified',
        method: 'normalized_quote',
        source_content_hash: 'hash',
        reason: '',
        semantic_status: 'unsupported',
        semantic_confidence: 0.8,
        semantic_reason: '',
        claim_id: 'claim-1',
        consistency_status: 'not_checked',
        contradicts_claim_ids: [],
        contradiction_reason: '',
        corroboration_status: 'corroborated',
        independent_source_count: 2,
        corroborates_claim_ids: ['claim-2'],
        corroboration_reason: '第二个独立来源支持该论断',
      },
    }

    expect(summarizeEvidence([base])).toEqual({
      records: 1,
      verbatimMatched: 1,
      semanticallySupported: 0,
      corroborated: 1,
      conflicted: 0,
    })
  })
})

describe('citedSources：稀疏引用不能被重编号', () => {
  it('保留原始序号，缺号处留空而不是把后面的往前挪', () => {
    // [1] 解析失败、[2] 成功时，[2] 必须仍然是 2。若压成列表第 1 项，
    // 正文里的 [2] 就会指向标着 [1] 的条目——读者据此核对会核到错的来源。
    const targets: (string | undefined)[] = []
    targets[1] = 'https://b.example.com'
    targets[3] = 'https://d.example.com'

    expect(citedSources(targets)).toEqual([
      { n: 2, url: 'https://b.example.com' },
      { n: 4, url: 'https://d.example.com' },
    ])
  })

  it('连续引用按原序号铺开', () => {
    expect(citedSources(['https://a.example.com', 'https://b.example.com'])).toEqual([
      { n: 1, url: 'https://a.example.com' },
      { n: 2, url: 'https://b.example.com' },
    ])
  })
})

describe('referenceTextFor：屏幕与纸面共用同一串来源文本', () => {
  const withReference: Finding = {
    statement: 'claim',
    source_url: 'https://a.example.com',
    evidence_quote: 'q',
    confidence: 0.9,
    verification: {
      status: 'verified',
      method: 'normalized_quote',
      source_content_hash: 'hash',
      source_reference: 'Cai 等. DAUHST. NeurIPS, 2022. https://a.example.com',
      reason: '',
      semantic_status: 'supported',
      semantic_confidence: 0.9,
      semantic_reason: '',
      claim_id: 'c1',
      consistency_status: 'clear',
      contradicts_claim_ids: [],
      contradiction_reason: '',
      corroboration_status: 'single_source',
      independent_source_count: 1,
      corroborates_claim_ids: [],
      corroboration_reason: '',
    },
  }

  it('优先落库的学术引用', () => {
    expect(referenceTextFor([withReference], 'https://a.example.com')).toBe(
      'Cai 等. DAUHST. NeurIPS, 2022. https://a.example.com',
    )
  })

  it('该来源没有学术引用时回退裸 URL', () => {
    expect(referenceTextFor([withReference], 'https://other.example.com')).toBe(
      'https://other.example.com',
    )
  })
})

describe('stripTrailingReferences：与后端 _body 同口径', () => {
  it('剥掉正文尾部 Synthesizer 追加的参考来源段', () => {
    const md = '正文 [1]。\n\n## 参考来源\n[1] https://a.example.com\n'
    expect(stripTrailingReferences(md)).toBe('正文 [1]。')
  })

  it('正文中间提到「参考来源」的普通小节不受影响', () => {
    const md = '## 参考来源的可靠性\n\n本节讨论来源质量。\n\n## 结论\n\n完。'
    expect(stripTrailingReferences(md)).toBe(md)
  })

  it('没有该段落时原样返回', () => {
    expect(stripTrailingReferences('只有正文。')).toBe('只有正文。')
  })
})
