import { AppIcon, type AppIconName } from './AppIcon'
import type { RunNarrative as Narrative } from '../types'

const STATUS_ICON: Record<string, AppIconName> = {
  done: 'check-circle',
  needs_review: 'alert',
  active: 'loader',
  error: 'circle-x',
  pending: 'circle-dashed',
}

/**
 * 人话进度：按「理解任务 → 检索核验 → 补洞 → 撰写 → 完成」分段，每段一两句结论。
 * 与下方的机器事件时间线互补——想知道「进行到哪了」看这里，想排查细节看时间线。
 */
export default function RunNarrative({ narrative }: { narrative: Narrative | undefined }) {
  if (!narrative) return null
  return (
    <section className="panel run-narrative" aria-labelledby="run-narrative-title">
      <div className="panel-header">
        <div>
          <span className="panel-kicker">进展</span>
          <h2 className="panel-title" id="run-narrative-title">
            {narrative.headline}
          </h2>
        </div>
      </div>
      <ol className="narrative-steps">
        {narrative.sections.map((section) => (
          <li key={section.key} className={`narrative-step is-${section.status}`}>
            <AppIcon
              name={STATUS_ICON[section.status] ?? 'circle-dashed'}
              size={16}
              className={section.status === 'active' ? 'spin' : ''}
              aria-hidden="true"
            />
            <div>
              <strong>{section.title}</strong>
              {section.lines.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}
