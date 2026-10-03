import QaAnswerBody from './QaAnswerBody'
import QaActivityView from './QaActivityView'
import { savedQaActivity } from '../lib/qaActivity'
import { verificationText } from '../lib/verificationText'
import { AppIcon } from './AppIcon'
import type { QaEvidence, QaMessage } from '../types'

const THOUGHT_LABEL: Record<string, string> = {
  rewrite: '检索式',
  model_knowledge: '模型知识',
  skip_search: '跳过检索',
  paper_read: '查阅本论文',
  paper_read_plan: '补读安排',
  evidence_coverage: '待确认内容',
  search_and_verify: '检索与逐字核验',
  citation_check: '引用复核',
  claim_check: '结论依据核对',
  answer_revision: '回答修订',
}

/**
 * 一轮问答。正文引用直接定位本论文，外部引用打开来源。
 */
export default function QaMessageView({
  message,
  onLocate,
  onReconnect,
}: {
  message: QaMessage
  onLocate?: (evidence: QaEvidence) => void
  onReconnect?: () => void
}) {
  const unresolved = message.thoughts
    .filter((thought) => thought.tool === 'evidence_coverage')
    .flatMap((thought) => thought.unresolved_topics ?? [])
  const steps = message.thoughts.filter(
    (thought) =>
      ![
        'model_reasoning',
        'model_usage',
        'paper_cache',
        'citation_binding',
        'extraction_audit',
      ].includes(thought.tool),
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
          {(message.status === 'pending' || message.status === 'running') && (
            <div role="status">
              <p>
                {message.status === 'pending'
                  ? '正在等待前一个问题完成…'
                  : '本轮正在生成，结果会保存到当前会话。'}
              </p>
              {onReconnect && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={onReconnect}>
                  恢复连接
                </button>
              )}
            </div>
          )}
          {message.status === 'error' && (
            <p className="error-text" role="alert">
              {message.error || '本轮处理失败，可以重新提问。'}
            </p>
          )}
          <QaActivityView items={savedQaActivity(message.thoughts)} />
          {unresolved.length > 0 && (
            <p className="muted" role="note">
              仍待确认：{unresolved.join('；')}
            </p>
          )}
          <div className="markdown-body qa-answer-body">
            <QaAnswerBody
              text={message.answer}
              citations={message.citations}
              evidence={message.evidence}
              binding={
                message.thoughts.find((thought) => thought.tool === 'citation_binding')?.binding
              }
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
                    <span>{verificationText(thought.observation, '暂无进一步说明。')}</span>
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
