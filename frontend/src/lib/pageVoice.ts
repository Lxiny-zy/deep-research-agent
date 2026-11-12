import type { PageKey } from './pageMeta'

/**
 * 各页主题导语：紧扣循证科研、原文溯源与严谨交付的核心方法论。
 *
 * 导语作视觉主锚点，下方配具体场景说明；
 * 操作按钮、错误反馈、确认提示与核验状态保持准确直白。
 */
export interface PageVoice {
  verse: string
  /** 引用典籍原句时的出处说明；产品自拟导语留空 */
  source?: string
}

export const PAGE_VOICE: Record<PageKey, PageVoice> = {
  home: { verse: '立论必先穷源，落笔皆有实据。' },
  qa: { verse: '审问慎思，孤证不立。' },
  run: { verse: '实据入册，存疑留白。' },
  reader: { verse: '披文相质，逐句对勘。' },
  history: { verse: '前案可稽，新知可续。' },
  library: { verse: '蓄积一手文献，自成私域证库。' },
  workflows: { verse: '博学之，审问之，慎思之，明辨之，笃行之。', source: '《中庸》' },
  agents: { verse: '专司其职，交叉核验。' },
  tools: { verse: '先立规矩，而后求真。' },
}
