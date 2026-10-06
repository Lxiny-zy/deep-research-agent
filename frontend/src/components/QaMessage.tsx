import QaAnswerBody from './QaAnswerBody'
import QaActivityView from './QaActivityView'
import QaAvatar from './QaAvatar'
import { savedQaActivity } from '../lib/qaActivity'
import { verificationText } from '../lib/verificationText'
import { AppIcon } from './AppIcon'
import type { QaEvidence, QaMessage } from '../types'

const THOUGHT_LABEL: Record<string, string> = {
  rewrite: '检索式',
  model_knowledge: '模型知识',
  skip_search: '跳过检索',
  paper_read: '查阅本论文',
  task_read: '查阅任务材料',
  paper_read_plan: '补读安排',
  evidence_coverage: '待确认内容',
  search_and_verify: '检索与逐字核验',
  citation_check: '引用复核',
  claim_check: '结论依据核对',
  answer_revision: '回答修订',
  conversation_context: '对话上下文',
  model_budget: '本轮模型调用',
  search_query_plan: '检索规划',
  evidence_verification: '资料核验状态',
  context_selection: '本轮证据范围',
  partial_answer: '保留可核验内容',
}

/**
 * 一轮问答。正文引用直接定位本论文，外部引用打开来源。
 */
export default function QaMessageView({
  message,
  onLocate,
  onReconnect,
  onRevise,
  revisionPending,
  onStop,
  onResume,
  stopping = false,
  continuationStatus,
}: {
  message: QaMessage
  onLocate?: (evidence: QaEvidence) => void
  onReconnect?: () => void
  onRevise?: () => void
  revisionPending?: boolean
  onStop?: () => void
  onResume?: () => void
  stopping?: boolean
  continuationStatus?: QaMessage['status']
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
        <QaAvatar active={message.status === 'pending' || message.status === 'running'} />
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
              {onStop && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={onStop}
                  disabled={stopping}
                >
                  {stopping ? '正在停止…' : '停止本轮'}
                </button>
              )}
            </div>
          )}
          {message.status === 'error' && (
            <p className="error-text" role="alert">
              {message.error || '本轮处理失败，可以重新提问。'}
            </p>
          )}
          {message.status === 'cancelled' && (
            <p role="status">本轮已停止，历史与已保存阶段仍保留。</p>
          )}
          {['error', 'cancelled'].includes(message.status) && message.recovery && (
            <div className="qa-recovery">
              {continuationStatus ? (
                <p className="muted small" role="status">
                  {['pending', 'running'].includes(continuationStatus)
                    ? '已继续，后续轮次正在处理。'
                    : ['done', 'fallback'].includes(continuationStatus)
                      ? '已继续，请查看后续回答。'
                      : '后续轮次尚未完成，请在后续轮次查看可继续的阶段。'}
                </p>
              ) : message.recovery.available && onResume ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  disabled={revisionPending}
                  onClick={onResume}
                  title="沿用原问题、材料范围和已保存阶段，创建新的请求"
                >
                  继续未完成的回答
                </button>
              ) : (
                <p className="muted small">
                  {message.recovery.reason || '没有可复用的阶段，无法继续本轮。'}
                </p>
              )}
              {message.recovery.available && !continuationStatus && (
                <p className="muted small">
                  {message.recovery.stage === 'draft'
                    ? '保留的是未核验草稿，继续后仍需完成核验。'
                    : message.recovery.stage === 'reviewed'
                      ? '将复用已保存的核验阶段。'
                      : '将复用已保存证据。'}
                </p>
              )}
            </div>
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
          {message.status === 'fallback' && message.revision?.available && onRevise && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={onRevise}
              disabled={revisionPending}
              title="复用这条回答的原稿与证据"
            >
              继续修订回答
            </button>
          )}
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
