import QaAnswerBody from './QaAnswerBody'
import { AppIcon } from './AppIcon'
import type { QaActivity } from '../types'
import QaActivityView from './QaActivityView'
import QaAvatar from './QaAvatar'

export default function QaStreamingAnswer({
  text,
  waiting,
  activity = [],
  requiresVerification = false,
}: {
  text: string
  waiting: string
  activity?: QaActivity[]
  requiresVerification?: boolean
}) {
  const stage = [...activity]
    .reverse()
    .find((item) => item.type === 'status' || item.type === 'reset')
  return (
    <div className="qa-answer-row">
      <QaAvatar active />
      <div className="qa-answer">
        <div className="qa-answer-pending" role="status">
          <AppIcon name="loader" size={15} className="spin" aria-hidden="true" />
          {stage?.message || (text ? '正在生成回答…' : waiting)}
        </div>
        <QaActivityView items={activity} live />
        {text && requiresVerification && (
          <p className="qa-draft-notice" role="note">
            回答草稿 · 正在核验，完成前可能调整
          </p>
        )}
        {text && (
          <div
            className="markdown-body qa-answer-body live-markdown"
            data-testid="qa-streaming-answer"
          >
            <QaAnswerBody text={text} streaming />
          </div>
        )}
      </div>
    </div>
  )
}
