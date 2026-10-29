import type { ResearchEvent, RunDetail } from '../types'

/**
 * 一问一谱：把一次研究读成一组思路线。
 *
 * 每个子问题是一条线。通过逐字核验的证据点亮它（亮线，已证之事）；检索过却没有
 * 可用证据，它就留暗（暗线，未解之问）；还没轮到检索的线待显。亮暗只由证据决定，
 * 不由模型的措辞决定，所以这张谱无从修饰。
 */
export type ThoughtState = 'lit' | 'dark' | 'pending'

export interface ThoughtLine {
  question: string
  /** 0 为初次拆解；n 为第 n 轮反思补洞新增 */
  round: number
  /** 通过逐字核验、可进入报告的发现数 */
  verified: number
  /** 模型提出的候选发现数 */
  candidates: number
  state: ThoughtState
}

/** 谱纹里的一条线：只需要状态与亮度。 */
export interface StripLine {
  state: ThoughtState
  verified: number
}

const normalize = (question: string) => question.trim().replace(/\s+/g, ' ')

const count = (value: unknown) => {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
}

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []

function settle(line: ThoughtLine, researched: boolean, finished: boolean): ThoughtState {
  if (line.verified > 0) return 'lit'
  // 运行结束仍未检索到的思路同样是未解之问
  return researched || finished ? 'dark' : 'pending'
}

/**
 * 从事件流读出思路线；事件里没有拆解记录时（较早的任务、或事件尚未回放）退回详情里的
 * 子问题与发现。同一思路被重复检索时取最好的一次，不重复计数。
 */
export function deriveThoughtLines(
  events: readonly ResearchEvent[],
  detail: Pick<RunDetail, 'sub_questions' | 'results'> | null | undefined,
  finished: boolean,
): ThoughtLine[] {
  const lines: ThoughtLine[] = []
  const byQuestion = new Map<string, ThoughtLine>()
  const researched = new Set<ThoughtLine>()
  const add = (question: string, round: number) => {
    const key = normalize(question)
    if (!key) return null
    let line = byQuestion.get(key)
    if (!line) {
      line = { question: key, round, verified: 0, candidates: 0, state: 'pending' }
      byQuestion.set(key, line)
      lines.push(line)
    }
    return line
  }

  let round = 0
  for (const event of events) {
    const data = event.data
    if (!data || typeof data !== 'object') continue
    if (event.stage === 'PLANNER') {
      for (const question of strings(data.sub_questions)) add(question, 0)
    } else if (event.stage === 'REFLECTOR' && Array.isArray(data.new_sub_questions)) {
      round += 1
      for (const question of strings(data.new_sub_questions)) add(question, round)
    } else if (
      event.stage === 'RESEARCHER' &&
      event.type === 'finding' &&
      typeof data.sub_question === 'string'
    ) {
      const line = add(data.sub_question, round)
      if (!line) continue
      researched.add(line)
      line.verified = Math.max(line.verified, count(data.verified_count ?? data.count))
      line.candidates = Math.max(line.candidates, count(data.candidate_count))
    }
  }

  if (lines.length === 0 && detail) {
    for (const sub of detail.sub_questions ?? []) add(sub.question, 0)
    for (const result of detail.results ?? []) {
      const line = add(result.sub_question, 0)
      if (!line) continue
      researched.add(line)
      // 与任务列表谱纹同一口径：逐字核验通过且语义支持
      const lit = result.findings.filter(
        (finding) =>
          finding.verification?.status === 'verified' &&
          finding.verification.semantic_status === 'supported',
      ).length
      line.verified = Math.max(line.verified, lit)
      line.candidates = Math.max(line.candidates, result.findings.length)
    }
  }

  for (const line of lines) line.state = settle(line, researched.has(line), finished)
  return lines
}

/** 任务列表里的谱纹：-1 未检索，0 无可用证据，n 为证据数。 */
export function stripFromSummary(spectrum: readonly number[] | undefined, finished: boolean) {
  return (spectrum ?? []).map(
    (value): StripLine => ({
      state: value > 0 ? 'lit' : value === 0 || finished ? 'dark' : 'pending',
      verified: Math.max(0, value),
    }),
  )
}

/** 谱纹横跨的波段：两端颜色太暗，不用。 */
export const STRIP_FROM = 400
export const STRIP_TO = 700

/** 第 index 条（共 total 条）在光谱上的位置：410–690 nm 均匀排开，颜色随位置由紫到红。 */
export function thoughtNm(index: number, total: number): number {
  return 410 + ((index + 0.5) * 280) / Math.max(1, total)
}

/** 第 index 条线在谱纹上的横向比例（0..1），供叠加在上面的 HTML 标记对齐。 */
export function stripX(index: number, total: number): number {
  return (thoughtNm(index, total) - STRIP_FROM) / (STRIP_TO - STRIP_FROM)
}

/** 亮度：一条证据即亮，四条及以上最亮。 */
export function lineGlow(verified: number): number {
  return verified <= 0 ? 0 : 0.55 + 0.45 * Math.min(1, (verified - 1) / 3)
}

export function tallyLines(lines: readonly { state: ThoughtState }[]) {
  const tally = { lit: 0, dark: 0, pending: 0 }
  for (const line of lines) tally[line.state] += 1
  return tally
}
