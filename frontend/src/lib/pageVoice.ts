import type { SpectrumKey } from './spectralLines'

/**
 * 各页题句：大字寄意，一页一句，讲求知、证据与诚实面对未知。
 *
 * 自拟的题句不冒充古籍；引用原句必须写明出处。下方的白话说明写在各页里，
 * 按钮、错误、删除确认与核验状态保持直白，不在这里。
 */
export interface PageVoice {
  verse: string
  /** 题句引自典籍时的出处，如「《中庸》」；自拟的题句不写 */
  source?: string
}

export const PAGE_VOICE: Record<SpectrumKey, PageVoice> = {
  home: { verse: '思有千线，终汇一谱。' },
  qa: { verse: '问不厌深，答必有据。' },
  run: { verse: '亮处已证，暗处待问。' },
  reader: { verse: '逐字照读，明暗自见。' },
  history: { verse: '旧谱重读，暗线或明。' },
  library: { verse: '片纸皆光，待问而明。' },
  workflows: { verse: '博学之，审问之，慎思之，明辨之，笃行之。', source: '《中庸》' },
  agents: { verse: '各执一线，同成一谱。' },
  tools: { verse: '器正而后谱真。' },
}
