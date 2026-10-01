import QaAnswerBody from './QaAnswerBody'
import { AppIcon } from './AppIcon'
import type { QaActivity } from '../types'
import QaActivityView from './QaActivityView'

export default function QaStreamingAnswer({
  text,
  waiting,
  activity = [],
}: {
  text: string
  waiting: string
  activity?: QaActivity[]
}) {
  const stage = [...activity]
    .reverse()
    .find((item) => item.type === 'status' || item.type === 'reset')
  return (
    <div className="qa-answer">
      <div className="qa-answer-pending" role="status">
        <AppIcon name="loader" size={15} className="spin" aria-hidden="true" />
        {text ? '正在生成回答，完成后核对引用…' : stage?.message || waiting}
      </div>
      <QaActivityView items={activity} live />
      {text && (
        <div
          className="markdown-body qa-answer-body live-markdown"
          data-testid="qa-streaming-answer"
        >
          <QaAnswerBody text={text} streaming />
        </div>
      )}
    </div>
  )
}
