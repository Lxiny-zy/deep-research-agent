import type { ResearchEvent } from '../types'
import {
  deriveThoughtLines,
  lineGlow,
  stripFromSummary,
  tallyLines,
  thoughtHue,
  thoughtNm,
} from './thoughtLines'

const event = (stage: string, type: string, data: Record<string, unknown>): ResearchEvent => ({
  stage,
  type: type as ResearchEvent['type'],
  message: '',
  elapsed: 0,
  data,
})

describe('research spectrum', () => {
  it('lights a line only where verified evidence exists', () => {
    const events = [
      event('PLANNER', 'info', { sub_questions: ['甲', '乙', '丙'] }),
      event('RESEARCHER', 'finding', { sub_question: '甲', verified_count: 2, candidate_count: 5 }),
      event('RESEARCHER', 'finding', { sub_question: '乙', verified_count: 0, candidate_count: 3 }),
    ]
    const lines = deriveThoughtLines(events, null, false)
    expect(lines.map((line) => [line.question, line.state, line.verified])).toEqual([
      ['甲', 'lit', 2],
      ['乙', 'dark', 0],
      ['丙', 'pending', 0],
    ])
    expect(tallyLines(lines)).toEqual({ lit: 1, dark: 1, pending: 1 })
  })

  it('adds reflection rounds and treats unresearched lines as unresolved once finished', () => {
    const events = [
      event('PLANNER', 'info', { sub_questions: ['甲'] }),
      event('RESEARCHER', 'finding', { sub_question: '甲', verified_count: 1 }),
      event('REFLECTOR', 'info', { gaps: ['缺口'], new_sub_questions: ['补一', '补二'] }),
      event('RESEARCHER', 'finding', { sub_question: '补一', verified_count: 3 }),
      // 重放或重试带来的重复事件不应把证据数翻倍
      event('RESEARCHER', 'finding', { sub_question: '补一', verified_count: 3 }),
    ]
    const lines = deriveThoughtLines(events, null, true)
    expect(lines.map((line) => [line.question, line.round, line.state, line.verified])).toEqual([
      ['甲', 0, 'lit', 1],
      ['补一', 1, 'lit', 3],
      ['补二', 1, 'dark', 0],
    ])
  })

  it('falls back to persisted findings with the same rule as the task list', () => {
    const verified = (semantic: 'supported' | 'unsupported') =>
      ({ verification: { status: 'verified', semantic_status: semantic } }) as never
    const lines = deriveThoughtLines(
      [],
      {
        sub_questions: [{ question: '甲' }, { question: '乙' }] as never,
        results: [
          { sub_question: '甲', findings: [verified('supported'), verified('unsupported')] },
        ],
      },
      true,
    )
    expect(lines.map((line) => [line.state, line.verified])).toEqual([
      ['lit', 1],
      ['dark', 0],
    ])
  })

  it('reads the list fingerprint and keeps positions and brightness in range', () => {
    expect(stripFromSummary([2, 0, -1], false).map((line) => line.state)).toEqual([
      'lit',
      'dark',
      'pending',
    ])
    expect(stripFromSummary([-1], true)[0].state).toBe('dark')
    expect(stripFromSummary(undefined, true)).toEqual([])
    expect(thoughtNm(0, 1)).toBe(550)
    expect(thoughtNm(0, 4)).toBeGreaterThanOrEqual(410)
    expect(thoughtNm(3, 4)).toBeLessThanOrEqual(690)
    expect(lineGlow(0)).toBe(0)
    expect(lineGlow(1)).toBeCloseTo(0.55)
    expect(lineGlow(9)).toBe(1)
  })

  it('colours list lines from violet to red in order', () => {
    const hues = Array.from({ length: 8 }, (_, index) => thoughtHue(index, 8))
    expect(hues[0]).toBeGreaterThan(270)
    expect(hues[7]).toBeLessThan(50)
    expect(hues.every((hue, index) => index === 0 || hue < hues[index - 1])).toBe(true)
  })
})
