import { useLiveTelemetryDemo } from '../hooks/useLiveTelemetryDemo'
import OrchestrationPipeline from './OrchestrationPipeline'
import StatsBar from './StatsBar'

export default function WelcomeTelemetrySection() {
  const demo = useLiveTelemetryDemo(true)

  return (
    <section className="welcome-telemetry-section" aria-labelledby="welcome-telemetry-title">
      <header className="welcome-telemetry-heading">
        <div>
          <span>OBSERVABLE BY DEFAULT</span>
          <h2 id="welcome-telemetry-title">研究不是黑箱，过程实时可见。</h2>
        </div>
        <p>
          从工作流阶段、累计 Token 到实际耗时，运行状态通过 SSE 持续更新；
          下面是零 API 消耗的自动循环演示。
        </p>
      </header>

      <div className="welcome-telemetry-demo">
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
      </div>

      <footer className="welcome-telemetry-caption">
        <span><i /> 实时阶段进度</span>
        <span><i /> Token 用量校准</span>
        <span><i /> 断线轮询兜底</span>
      </footer>
    </section>
  )
}
