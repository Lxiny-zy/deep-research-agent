import QaAnswerBody from './QaAnswerBody'
import QaActivityView from './QaActivityView'
import { savedQaActivity } from '../lib/qaActivity'
import { AppIcon } from './AppIcon'
import type { QaEvidence, QaMessage } from '../types'

const THOUGHT_LABEL: Record<string, string> = {
  rewrite: '检索式',
  model_knowledge: '模型知识',
  skip_search: '跳过检索',
  paper_read: '查阅本论文',
  search_and_verify: '检索与逐字核验',
  citation_check: '引用复核',
  answer_revision: '回答修订',
}

/**
 * 一轮问答。正文引用直接定位本论文，外部引用打开来源。
 */
export default function QaMessageView({
  message,
  onLocate,
}: {
  message: QaMessage
  onLocate?: (evidence: QaEvidence) => void
}) {
  const steps = message.thoughts.filter(
    (thought) => !['model_reasoning', 'model_usage', 'paper_cache'].includes(thought.tool),
  )
  return (
    <article className="qa-turn" aria-label={`第 ${message.position + 1} 轮问答`}>
      <div className="qa-question">
        <p>{message.query}</p>
      </div>
      <div className="qa-answer-row">
        <span className="qa-avatar" aria-hidden="true">
          <AppIcon name="network" size={14} strokeWidth={2} />
        </span>
        <div className={`qa-answer${message.status === 'fallback' ? ' is-fallback' : ''}`}>
          <QaActivityView items={savedQaActivity(message.thoughts)} />
          <div className="markdown-body qa-answer-body">
            <QaAnswerBody
              text={message.answer}
              citations={message.citations}
              evidence={message.evidence}
              onLocate={onLocate}
            />
          </div>
          {steps.length > 0 && (
            <details className="qa-thoughts">
              <summary>
                <AppIcon name="chevron-right" size={13} aria-hidden="true" />
                检索与核验过程
              </summary>
              <ol>
                {steps.map((thought, index) => (
                  <li key={index}>
                    <strong>{THOUGHT_LABEL[thought.tool] ?? thought.tool}</strong>
                    <span>{thought.observation}</span>
                  </li>
                ))}
              </ol>
            </details>
          )}
        </div>
      </div>
    </article>
  )
}
