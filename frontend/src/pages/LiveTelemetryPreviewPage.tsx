import OrchestrationPipeline from '../components/OrchestrationPipeline'
import StatsBar from '../components/StatsBar'
import { telemetryStageMessage, useLiveTelemetryDemo } from '../hooks/useLiveTelemetryDemo'

export default function LiveTelemetryPreviewPage() {
  const demo = useLiveTelemetryDemo()

  return (
    <main className="live-preview-page signal-theme">
      <div className="ambient-stage" aria-hidden="true">
        <span className="ambient-block block-coral" />
        <span className="ambient-block block-blue" />
        <span className="ambient-block block-lime" />
        <span className="ambient-grid" />
      </div>

      <div className="live-preview-content">
        <header className="live-preview-header">
          <div>
            <span>LIVE TELEMETRY PREVIEW</span>
            <h1>研究进度动态效果</h1>
            <p>这是无真实模型消耗的模拟运行，展示生产组件的实际动画与状态切换。</p>
          </div>
          <button className="btn btn-primary" onClick={demo.replay}>重新播放</button>
        </header>

        <StatsBar
          stats={demo.done ? { elapsed: 26, total_tokens: 6384, sources: 12, tokens_estimated: false } : null}
          detail={null}
          progress={demo.progress}
          live={{ elapsed: demo.elapsed, tokens: demo.tokens, findings: demo.findings }}
          liveActive={!demo.done}
          connectionStatus={demo.done ? 'done' : 'streaming'}
          tokensEstimated={!demo.done}
        />
        <OrchestrationPipeline execution={demo.execution} events={demo.events} runStatus={demo.runStatus} />

        <section className="live-preview-notes">
          <strong>当前状态</strong>
          <span>{telemetryStageMessage(demo.elapsed)}</span>
          <small>{demo.done ? '演示完成，可点击「重新播放」再次查看。' : '数字、轨道和状态会根据模拟事件连续过渡。'}</small>
        </section>
      </div>
    </main>
  )
}
