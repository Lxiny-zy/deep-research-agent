/**
 * 页面目录：整站按一本学术期刊编排，每个页面是一节。
 * 侧栏是目录，顶栏是页眉，页头的「§ 编号 · 栏目」与题句都从这里取值。
 */

export interface PageMeta {
  /** 栏目名：立题、研讨…… */
  stage: string
  /** 期刊式节号，两位数字 */
  section: string
  /** 这一节在研究里做什么：一句话，供提示与无障碍说明 */
  note: string
}

export const PAGE_META = {
  home: { stage: '立题', section: '01', note: '设定研究命题、任务契约与检索规模，启动可溯源的科研任务。' },
  qa: { stage: '研讨', section: '02', note: '围绕概念、文献与方法细节开展多轮学术问答，回答附原文出处。' },
  run: { stage: '求证', section: '03', note: '实时追踪子问题检索、原文逐字核验、缺口补证与多格式交付。' },
  reader: { stage: '研读', section: '04', note: '对照 PDF 原文与引文定位，逐段核查论文方法、图表与核心论据。' },
  history: { stage: '归档', section: '05', note: '检索与回溯历史研究记录、核验指标及交付文件，支持随时续问。' },
  library: { stage: '文献', section: '06', note: '按课题归集与审核私有文献资料，在研究与问答中同权参与核验。' },
  workflows: { stage: '编排', section: '07', note: '选用预设研究链路，或可视化编排多智能体协作与反思流程。' },
  agents: { stage: '协同', section: '08', note: '管理规划、检索、复核与写作角色的提示词、模型档案及检索凭据。' },
  tools: { stage: '准则', section: '09', note: '配置全局默认模型、并发阈值、双源门禁与学术交付验收标准。' },
} satisfies Record<string, PageMeta>

export type PageKey = keyof typeof PAGE_META

export function pageKey(pathname: string): PageKey {
  if (pathname === '/') return 'home'
  if (pathname.startsWith('/qa')) return 'qa'
  if (pathname.startsWith('/history')) return 'history'
  if (pathname.startsWith('/library')) return 'library'
  if (/^\/runs\/[^/]+\/read/.test(pathname)) return 'reader'
  if (pathname.startsWith('/runs/')) return 'run'
  if (pathname.startsWith('/workflows')) return 'workflows'
  if (pathname.startsWith('/agents')) return 'agents'
  return 'tools'
}

const ROMAN: [number, string][] = [
  [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I'],
]

function roman(value: number): string {
  let rest = value
  let out = ''
  for (const [n, s] of ROMAN) {
    while (rest >= n) {
      out += s
      rest -= n
    }
  }
  return out
}

/** 期刊式卷期号：卷 = 年份序（2016 起为第 I 卷），期 = 月份。 */
export function issueLabel(date = new Date()): string {
  const volume = Math.max(1, date.getFullYear() - 2015)
  return `Vol. ${roman(volume)} · No. ${date.getMonth() + 1}`
}
