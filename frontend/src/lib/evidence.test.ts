import type { Finding, ResearchEvent } from '../types'
import { countBlockedSources, resolveCitationTargets, summarizeEvidence } from './evidence'

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
      },
    }

    expect(summarizeEvidence([base])).toEqual({
      records: 1,
      verbatimMatched: 1,
      semanticallySupported: 0,
      conflicted: 0,
    })
  })
})
