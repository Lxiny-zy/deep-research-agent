import { useEffect, useRef, useState } from 'react'
import type { ResearchProgress } from '../lib/runProgress'
import type { RunDetail, RunStats } from '../types'
import { AppIcon } from './AppIcon'

interface LiveStats {
  elapsed: number
  tokens: number
  findings: number
}

function useAnimatedNumber(target: number, duration = 480): number {
  const [value, setValue] = useState(target)
  const current = useRef(target)

  useEffect(() => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduceMotion || duration <= 0) {
      current.current = target
      setValue(target)
      return
    }
    const start = current.current
    const delta = target - start
    if (Math.abs(delta) < 0.001) return
    const startedAt = performance.now()
    let frame = 0
    const animate = (now: number) => {
      const t = Math.min(1, (now - startedAt) / duration)
      const eased = 1 - Math.pow(1 - t, 3)
      const next = start + delta * eased
      current.current = next
      setValue(next)
      if (t < 1) frame = requestAnimationFrame(animate)
    }
    frame = requestAnimationFrame(animate)
    return () => cancelAnimationFrame(frame)
  }, [duration, target])

  return value
}

function useChangePulse(value: number, enabled: boolean): boolean {
  const previous = useRef(value)
  const [pulsing, setPulsing] = useState(false)
  useEffect(() => {
    if (!enabled || value === previous.current) {
      previous.current = value
      return
    }
    previous.current = value
    setPulsing(true)
    const timer = window.setTimeout(() => setPulsing(false), 520)
    return () => window.clearTimeout(timer)
  }, [enabled, value])
  return pulsing
}

function formatElapsed(value: number): string {
  if (value < 60) return `${value.toFixed(1)} 秒`
  const minutes = Math.floor(value / 60)
  const seconds = Math.floor(value % 60)
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

const numberFormat = new Intl.NumberFormat('zh-CN')

function LiveMetric({ icon, label, value, note, pulsing }: {
  icon: React.ReactNode
  label: string
  value: string
  note: string
  pulsing: boolean
}) {
  return (
    <div className={`live-metric${pulsing ? ' is-updating' : ''}`}>
      <span className="live-metric-icon">{icon}</span>
      <span className="live-metric-copy">
        <span className="live-metric-label">{label}</span>
        <strong className="live-metric-value">{value}</strong>
        <span className="live-metric-note">{note}</span>
      </span>
    </div>
  )
}

export default function StatsBar({
  stats,
  detail,
  progress,
  live = null,
  liveActive = false,
  connectionStatus = 'idle',
  tokensEstimated = false,
}: {
  stats: RunStats | null
  detail: RunDetail | null
  progress: ResearchProgress
  live?: LiveStats | null
  liveActive?: boolean
  connectionStatus?: 'idle' | 'streaming' | 'disconnected' | 'done' | 'error'
  tokensEstimated?: boolean
}) {
  const liveElapsed = live?.elapsed ?? 0
  const anchor = useRef({ base: liveElapsed, at: Date.now() })
  const [clock, setClock] = useState(Date.now())

  useEffect(() => {
    anchor.current = { base: liveElapsed, at: Date.now() }
  }, [liveElapsed])

  useEffect(() => {
    if (!liveActive) return
    const id = window.setInterval(() => setClock(Date.now()), 250)
    return () => window.clearInterval(id)
  }, [liveActive])

  const finalElapsed = stats?.elapsed ?? detail?.elapsed ?? 0
  const elapsedTarget = liveActive
    ? anchor.current.base + (clock - anchor.current.at) / 1000
    : finalElapsed
  const tokenTarget = stats?.total_tokens ?? (liveActive ? live?.tokens ?? 0 : detail?.total_tokens ?? 0)
  const sources = stats?.sources ?? detail?.report?.citations.length
  const findingTarget = sources ?? live?.findings ?? 0
  const isEstimate = stats?.tokens_estimated ?? tokensEstimated

  const elapsed = useAnimatedNumber(Math.max(0, elapsedTarget), 220)
  const tokens = useAnimatedNumber(Math.max(0, tokenTarget), 620)
  const findings = useAnimatedNumber(Math.max(0, findingTarget), 460)
  const animatedProgress = useAnimatedNumber(progress.percent, 720)

  const tokenPulse = useChangePulse(tokenTarget, liveActive)
  const findingPulse = useChangePulse(findingTarget, liveActive)
  const progressPulse = useChangePulse(progress.percent, liveActive)

  const connectionLabel =
    connectionStatus === 'disconnected'
      ? '连接恢复中'
      : liveActive
        ? '实时同步'
        : progress.percent >= 100
          ? '统计已确认'
          : '等待运行'

  return (
    <section className={`research-live-overview${liveActive ? ' is-live' : ''}`} aria-label="研究实时统计">
      <div className={`research-progress-summary${progressPulse ? ' is-updating' : ''}`}>
        <div className="research-progress-topline">
          <span className="research-progress-kicker">总体进度</span>
          <span className={`live-sync-state ${connectionStatus}`}>
            <i aria-hidden />
            {connectionLabel}
          </span>
        </div>

        <div className="research-progress-main">
          <strong className="research-progress-value">
            {Math.round(animatedProgress)}<small>%</small>
          </strong>
          <div className="research-progress-context">
            <span>{progress.currentLabel}</span>
            <small>
              {progress.total > 0
                ? `${progress.completed} / ${progress.total} 个阶段已处理`
                : '正在确认工作流阶段'}
              {progress.estimated && ' · 阶段估算'}
            </small>
          </div>
        </div>

        <div
          className="research-progress-track"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(animatedProgress)}
          aria-label="研究总体进度"
        >
          <span style={{ transform: `scaleX(${Math.max(0, Math.min(100, animatedProgress)) / 100})` }}>
            <i aria-hidden />
          </span>
        </div>
      </div>

      <div className="live-metrics">
        <LiveMetric
          icon={<AppIcon name="clock" size={19} aria-hidden="true" />}
          label="已耗时"
          value={formatElapsed(elapsed)}
          note={liveActive ? '持续计时中' : '本次运行总耗时'}
          pulsing={liveActive}
        />
        <LiveMetric
          icon={<AppIcon name="braces" size={19} aria-hidden="true" />}
          label="Token 消耗"
          value={`${isEstimate ? '≈ ' : ''}${numberFormat.format(Math.round(tokens))}`}
          note={isEstimate ? '流式阶段含估算值' : liveActive ? '随模型调用累计' : '最终累计用量'}
          pulsing={tokenPulse}
        />
        <LiveMetric
          icon={<AppIcon name="scan-search" size={19} aria-hidden="true" />}
          label={sources != null ? '引用来源' : '研究发现'}
          value={numberFormat.format(Math.round(findings))}
          note={sources != null ? '报告引用的有效来源' : '随检索结果动态增加'}
          pulsing={findingPulse}
        />
      </div>
    </section>
  )
}
