// 澄清对话框：研究开始**之前**问清楚，而不是跑完一次答非所问的研究。
//
// 选项是可点按钮而不是一段文本列表——这是这次改动的要点。旧实现把候选解读
// 渲染成一份报告里的 <li>，用户看完还得自己回到输入框重打一遍。
//
// 「直接研究」永远在：用户比系统更清楚自己要什么，任何时候都该能跳过追问。
import { useEffect, useRef, useState } from 'react'
import { MAX_CLARIFY_ROUNDS, SKIP_OPTION } from '../lib/clarification'
import { AppIcon } from './AppIcon'

interface Props {
  question: string
  options: string[]
  round: number
  /** 用户选了某个候选项，或填了自定义内容 */
  onAnswer: (answer: string) => void
  /** 放弃澄清，直接拿现有信息去研究 */
  onSkip: () => void
  busy?: boolean
}

export default function ClarifyDialog({
  question,
  options,
  round,
  onAnswer,
  onSkip,
  busy = false,
}: Props) {
  const [custom, setCustom] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // 每轮换问题时清空上一轮的输入，并把焦点交回输入框——没有候选项可点时
  // （问「对比哪几个」这类要具体名字的 gap），输入框就是唯一的作答方式。
  useEffect(() => {
    setCustom('')
    inputRef.current?.focus()
  }, [question])

  // 「直接研究」在后端也是一个普通候选项，但它的语义是终止循环，
  // 因此在 UI 上单独成键，不混在候选里让用户去找。
  const choices = options.filter((option) => option !== SKIP_OPTION)

  function submitCustom() {
    const value = custom.trim()
    if (value && !busy) onAnswer(value)
  }

  return (
    <section className="panel clarify-dialog" aria-label="澄清提问">
      <div className="clarify-head">
        <span className="panel-kicker">
          <AppIcon name="help" size={14} aria-hidden="true" /> 需要补充信息
        </span>
        <span className="muted small">
          第 {round + 1} / {MAX_CLARIFY_ROUNDS} 轮
        </span>
      </div>

      <p className="clarify-question">{question}</p>

      {choices.length > 0 && (
        <div className="clarify-options">
          {choices.map((option) => (
            <button
              type="button"
              key={option}
              className="clarify-option"
              disabled={busy}
              onClick={() => onAnswer(option)}
            >
              <span>{option}</span>
              <AppIcon name="arrow-right" size={14} aria-hidden="true" />
            </button>
          ))}
        </div>
      )}

      <label className="field-label clarify-custom" htmlFor="clarify-custom">
        {choices.length > 0 ? '或者自己描述' : '请补充'}
        <span className="clarify-custom-row">
          <input
            id="clarify-custom"
            ref={inputRef}
            className="input"
            value={custom}
            disabled={busy}
            placeholder="直接输入更具体的内容…"
            onChange={(event) => setCustom(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                submitCustom()
              }
            }}
          />
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy || !custom.trim()}
            onClick={submitCustom}
          >
            确定
          </button>
        </span>
      </label>

      <div className="clarify-foot">
        <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={onSkip}>
          {SKIP_OPTION}
        </button>
        <span className="hint">研究还没有开始，因此没有消耗检索与生成成本。</span>
      </div>
    </section>
  )
}
