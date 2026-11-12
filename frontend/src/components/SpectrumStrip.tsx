import { useId, type CSSProperties } from 'react'
import { lineGlow, stripX, type StripLine } from '../lib/thoughtLines'

const G = { width: 1000, height: 84, midY: 42 } as const

/**
 * 实证经纬刻线图（Empirical Barcode & Folio Rulings）：
 * 以精密光学干涉细刻线呈现每个子问题的真实核验状态——
 * - 已核验（lit）：深海翠墨实心细刻线 + 柔和微光晕，修长度随原文核验证据量伸展；
 * - 缺证据（dark）：上下琥珀短刻夹中央留白缺口，如实标示存疑边界；
 * - 检索中（pending）：孔雀墨青细虚线配柔和呼吸律动。
 */
export default function SpectrumStrip({
  lines,
  live = false,
  active = null,
  label,
  className = '',
}: {
  lines: readonly StripLine[]
  live?: boolean
  active?: number | null
  label?: string
  className?: string
}) {
  const uid = useId().replace(/:/g, '')
  const count = Math.max(1, lines.length)
  const coreWidth = Math.min(5, Math.max(2.4, Math.floor((G.width * 0.16) / count)))

  return (
    <svg
      className={`spectrum-strip${live ? ' is-live' : ''}${className ? ` ${className}` : ''}`}
      viewBox={`0 0 ${G.width} ${G.height}`}
      preserveAspectRatio="none"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <defs>
        <linearGradient id={`${uid}-band`} x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="var(--primary)" stopOpacity="0" />
          <stop offset="15%" stopColor="var(--primary)" stopOpacity="0.18" />
          <stop offset="50%" stopColor="var(--mark)" stopOpacity="0.22" />
          <stop offset="85%" stopColor="var(--primary)" stopOpacity="0.18" />
          <stop offset="100%" stopColor="var(--primary)" stopOpacity="0" />
        </linearGradient>
      </defs>

      <rect
        className="strip-folio-band"
        x={12}
        y={G.midY - 8}
        width={G.width - 24}
        height={16}
        fill={`url(#${uid}-band)`}
        rx={2}
      />
      <line
        className="strip-band"
        x1={16}
        x2={G.width - 16}
        y1={G.midY}
        y2={G.midY}
      />

      {lines.map((line, index) => {
        const x = stripX(index, lines.length) * G.width
        const isActive = active === index
        const cls = `strip-line is-${line.state}${isActive ? ' is-active' : ''}`

        if (line.state === 'lit') {
          const glow = lineGlow(line.verified)
          const half = Math.round(16 + glow * 18)
          const style = {
            '--glow': glow,
          } as CSSProperties
          return (
            <g key={`lit-${index}`} className={cls} style={style}>
              <rect
                className="strip-halo"
                x={x - coreWidth * 2.2}
                y={G.midY - half}
                width={coreWidth * 4.4}
                height={half * 2}
                rx={coreWidth * 2}
              />
              <rect
                className="strip-core"
                x={x - coreWidth / 2}
                y={G.midY - half}
                width={coreWidth}
                height={half * 2}
                rx={1}
              />
              <rect
                className="strip-cap"
                x={x - coreWidth * 1.1}
                y={G.midY - half}
                width={coreWidth * 2.2}
                height={1.8}
                rx={0.9}
              />
            </g>
          )
        }

        if (line.state === 'dark') {
          return (
            <g key={`dark-${index}`} className={cls}>
              <rect x={x - coreWidth / 2} y={G.midY - 14} width={coreWidth} height={7} rx={1} />
              <rect x={x - coreWidth / 2} y={G.midY + 7} width={coreWidth} height={7} rx={1} />
            </g>
          )
        }

        return (
          <g key={`pending-${index}`} className={cls}>
            <line
              x1={x}
              x2={x}
              y1={G.midY - 15}
              y2={G.midY + 15}
            />
          </g>
        )
      })}
    </svg>
  )
}
