import { useLiveTelemetryDemo } from '../hooks/useLiveTelemetryDemo'
import OrchestrationPipeline from './OrchestrationPipeline'
import StatsBar from './StatsBar'
import { AppIcon } from './AppIcon'

export default function WelcomeTelemetrySection() {
  const demo = useLiveTelemetryDemo(true)

  return (
    <section className="welcome-telemetry-section" aria-labelledby="welcome-telemetry-title">
      <header className="welcome-telemetry-heading">
        <div>
          <span>02 / RESEARCH IN MOTION</span>
          <h2 id="welcome-telemetry-title">每一步思考，都有迹可循。</h2>
        </div>
        <p>研究过程预览 · 演示数据</p>
      </header>

      <div className="welcome-telemetry-demo">
        <StatsBar
          stats={
            demo.done
              ? { elapsed: 26, total_tokens: 6384, sources: 12, tokens_estimated: false }
              : null
          }
          detail={null}
          progress={demo.progress}
          live={{ elapsed: demo.elapsed, tokens: demo.tokens, findings: demo.findings }}
          liveActive={!demo.done}
          connectionStatus={demo.done ? 'done' : 'streaming'}
          tokensEstimated={!demo.done}
        />
        <OrchestrationPipeline
          execution={demo.execution}
          events={demo.events}
          runStatus={demo.runStatus}
        />
      </div>

      <footer className="welcome-telemetry-caption">
        <span>
          <AppIcon name="activity" size={13} aria-hidden="true" /> 实时阶段进度
        </span>
        <span>
          <AppIcon name="braces" size={13} aria-hidden="true" /> 研究用量
        </span>
        <span>
          <AppIcon name="shield" size={13} aria-hidden="true" /> 可追溯的证据
        </span>
      </footer>
    </section>
  )
}
