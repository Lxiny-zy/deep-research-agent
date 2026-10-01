import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AppIcon } from './AppIcon'

export default function QaStreamingAnswer({ text, waiting }: { text: string; waiting: string }) {
  return (
    <div className="qa-answer">
      <div className="qa-answer-pending" role="status">
        <AppIcon name="loader" size={15} className="spin" aria-hidden="true" />
        {text ? '正在生成回答，完成后核对引用…' : waiting}
      </div>
      {text && (
        <div className="markdown-body live-markdown" data-testid="qa-streaming-answer">
          <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
            {text}
          </ReactMarkdown>
        </div>
      )}
    </div>
  )
}
