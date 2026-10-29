import { useId, type CSSProperties } from 'react'
import { rgbCss, wavelengthToRgb, type RGB } from '../lib/spectralLines'
import {
  lineGlow,
  STRIP_FROM,
  STRIP_TO,
  stripX,
  thoughtNm,
  type StripLine,
} from '../lib/thoughtLines'

const GEOMETRY = {
  full: { width: 1000, height: 120, bandY: 52, bandH: 16, litW: 3.2, darkW: 5 },
  mini: { width: 160, height: 20, bandY: 7, bandH: 6, litW: 1.6, darkW: 2.4 },
} as const

/** 浅色底上把谱线颜色压暗一些，保证对比度。 */
const toned = ([r, g, b]: RGB): RGB => [
  Math.round(r * 0.78),
  Math.round(g * 0.7),
  Math.round(b * 0.82),
]

/**
 * 谱纹：一组思路线画成的一道光谱。亮线是已证之事，暗线是未解之问，虚线待显。
 * 任务记录里是一枚小谱纹，任务页里是整幅研究谱；两处画法相同，只是尺寸不同。
 */
export default function SpectrumStrip({
  lines,
  size = 'mini',
  live = false,
  active = null,
  label,
  className = '',
}: {
  lines: readonly StripLine[]
  size?: 'mini' | 'full'
  /** 运行中：待显的线缓慢明灭 */
  live?: boolean
  /** 当前悬停或聚焦的那一条 */
  active?: number | null
  /** 无障碍说明；不给时整幅图对读屏隐藏（由外层文字说明） */
  label?: string
  className?: string
}) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '')
  const g = GEOMETRY[size]
  const full = size === 'full'
  const stops = Array.from({ length: 11 }, (_, i) => {
    const t = i / 10
    return { offset: t, color: rgbCss(wavelengthToRgb(STRIP_FROM + t * (STRIP_TO - STRIP_FROM))) }
  })

  return (
    <svg
      className={`spectrum-strip is-${size}${live ? ' is-live' : ''}${className ? ` ${className}` : ''}`}
      viewBox={`0 0 ${g.width} ${g.height}`}
      preserveAspectRatio="none"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <defs>
        <linearGradient id={`${uid}-band`} x1="0" x2="1" y1="0" y2="0">
          {stops.map((stop) => (
            <stop key={stop.offset} offset={stop.offset} stopColor={stop.color} />
          ))}
        </linearGradient>
        {full && (
          <>
            <linearGradient id={`${uid}-fade`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor="#fff" stopOpacity="0" />
              <stop offset="0.5" stopColor="#fff" stopOpacity="1" />
              <stop offset="1" stopColor="#fff" stopOpacity="0" />
            </linearGradient>
            <mask
              id={`${uid}-mask`}
              maskUnits="userSpaceOnUse"
              x="0"
              y="0"
              width={g.width}
              height={g.height}
            >
              <rect x="0" y="0" width={g.width} height={g.height} fill={`url(#${uid}-fade)`} />
            </mask>
            <filter id={`${uid}-glow`} x="-200%" y="-10%" width="500%" height="120%">
              <feGaussianBlur stdDeviation="5" />
            </filter>
          </>
        )}
      </defs>

      <rect
        className="strip-band"
        x={0}
        y={g.bandY}
        width={g.width}
        height={g.bandH}
        fill={`url(#${uid}-band)`}
      />

      <g mask={full ? `url(#${uid}-mask)` : undefined}>
        {lines.map((line, index) => {
          if (line.state !== 'lit') return null
          const nm = thoughtNm(index, lines.length)
          const x = stripX(index, lines.length) * g.width
          const rgb = wavelengthToRgb(nm)
          const style = {
            '--c': rgbCss(rgb),
            '--c-light': rgbCss(toned(rgb)),
            '--glow': lineGlow(line.verified),
          } as CSSProperties
          return (
            <g
              key={`lit-${index}`}
              className={`strip-line is-lit${active === index ? ' is-active' : ''}`}
              style={style}
            >
              {full && (
                <rect
                  className="strip-halo"
                  x={x - 10}
                  y={4}
                  width={20}
                  height={g.height - 8}
                  filter={`url(#${uid}-glow)`}
                />
              )}
              <rect
                className="strip-core"
                x={x - g.litW / 2}
                y={full ? 4 : 1}
                width={g.litW}
                height={full ? g.height - 8 : g.height - 2}
              />
            </g>
          )
        })}
      </g>

      {lines.map((line, index) => {
        if (line.state === 'lit') return null
        const x = stripX(index, lines.length) * g.width
        const cls = `strip-line is-${line.state}${active === index ? ' is-active' : ''}`
        if (line.state === 'dark') {
          // 暗线只刻在光谱带上：光到这里被截断，像未解之问留下的缺口
          return (
            <rect
              key={`dark-${index}`}
              className={cls}
              x={x - g.darkW / 2}
              y={g.bandY - (full ? 6 : 1)}
              width={g.darkW}
              height={g.bandH + (full ? 12 : 2)}
            />
          )
        }
        return (
          <line
            key={`pending-${index}`}
            className={cls}
            x1={x}
            x2={x}
            y1={full ? 22 : 3}
            y2={full ? g.height - 22 : g.height - 3}
          />
        )
      })}
    </svg>
  )
}
