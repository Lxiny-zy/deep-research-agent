import { useId, useState } from 'react'
import { PAGE_VOICE } from '../lib/pageVoice'
import { stripX, tallyLines, type ThoughtLine } from '../lib/thoughtLines'
import SpectrumStrip from './SpectrumStrip'

const STATE_TEXT = { lit: '已证', dark: '暗线', pending: '待显' } as const

function evidenceText(line: ThoughtLine): string {
  if (line.state === 'lit') {
    const extra = line.candidates > line.verified ? `（候选 ${line.candidates} 条）` : ''
    return `${line.verified} 条发现通过逐字核验${extra}`
  }
  if (line.state === 'dark') {
    return line.candidates > 0 ? `候选 ${line.candidates} 条，均未通过核验` : '未找到可以核验的证据'
  }
  return '尚在检索'
}

function describe(line: ThoughtLine, index: number): string {
  const round = line.round > 0 ? `，第 ${line.round} 轮补洞新增` : ''
  return `思路 ${index + 1}${round}，${STATE_TEXT[line.state]}：${line.question}。${evidenceText(line)}`
}

/**
 * 本题之谱：这次研究的每个子问题是一条线。
 * 有证据处亮起，检索过仍无证据处留暗，未检索的线待显；运行中随事件实时变化。
 */
export default function ResearchSpectrum({
  lines,
  live,
}: {
  lines: readonly ThoughtLine[]
  live: boolean
}) {
  const [active, setActive] = useState<number | null>(null)
  const headingId = useId()
  if (lines.length === 0) return null
  const tally = tallyLines(lines)
  const current = active == null ? null : (lines[active] ?? null)
  const left = (index: number) => stripX(index, lines.length) * 100

  return (
    <section className="panel research-spectrum" aria-labelledby={headingId}>
      <header className="research-spectrum-head">
        <div>
          <span className="panel-kicker">本题之谱</span>
          <h2 id={headingId}>
            {lines.length} 条思路
            <span className="is-lit">{tally.lit} 条已证</span>
            {tally.dark > 0 && <span className="is-dark">{tally.dark} 条暗线</span>}
            {tally.pending > 0 && <span className="is-pending">{tally.pending} 条待显</span>}
          </h2>
        </div>
        <p className="research-spectrum-verse">{PAGE_VOICE.run.verse}</p>
      </header>

      <div className="research-spectrum-stage" onMouseLeave={() => setActive(null)}>
        <SpectrumStrip lines={lines} live={live} active={active} />
        <ol className="research-spectrum-marks">
          {lines.map((line, index) => (
            <li key={index} style={{ left: `${left(index)}%` }}>
              <button
                type="button"
                className={`is-${line.state}${active === index ? ' is-active' : ''}`}
                aria-label={describe(line, index)}
                onMouseEnter={() => setActive(index)}
                onFocus={() => setActive(index)}
                onBlur={() => setActive(null)}
              >
                {index + 1}
              </button>
            </li>
          ))}
        </ol>
        {current && active != null && (
          <div
            className={`research-spectrum-tip is-${current.state}`}
            style={{ left: `${Math.min(84, Math.max(16, left(active)))}%` }}
            aria-hidden="true"
          >
            <span className="research-spectrum-tip-head">
              思路 {active + 1}
              {current.round > 0 && ` · 第 ${current.round} 轮补洞`}
              <em>{STATE_TEXT[current.state]}</em>
            </span>
            <strong>{current.question}</strong>
            <span>{evidenceText(current)}</span>
          </div>
        )}
      </div>

      <ul className="research-spectrum-legend" aria-hidden="true">
        <li className="is-lit">亮线 · 有逐字核验的证据</li>
        <li className="is-dark">暗线 · 检索过，尚无可核验的证据</li>
        {tally.pending > 0 && <li className="is-pending">待显 · 尚在检索</li>}
      </ul>
    </section>
  )
}
