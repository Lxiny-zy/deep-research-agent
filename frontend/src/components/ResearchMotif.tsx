import { useId } from 'react'

export type MotifKind = 'ribbons' | 'archive' | 'weave' | 'constellation' | 'orbit' | 'pulse'

// One palette and fine woven lines, with a distinct silhouette for each research space.
function strand(kind: MotifKind, index: number): string {
  const v = index / 31
  const points = Array.from({ length: 121 }, (_, step) => {
    const t = step / 120
    const a = t * Math.PI * 2
    let x: number
    let y: number
    switch (kind) {
      case 'archive': {
        const radius = 28 + v * 79
        x = 200 + Math.cos(a) * radius * 1.35 + Math.sin(a * 3) * 7
        y = 116 + Math.sin(a) * radius * 0.72 + v * 16 + Math.cos(a * 2) * 10
        break
      }
      case 'orbit': {
        const angle = v * Math.PI
        const u = Math.cos(a) * 119
        const w = Math.sin(a) * 37
        x = 200 + u * Math.cos(angle) - w * Math.sin(angle)
        y = 120 + (u * Math.sin(angle) + w * Math.cos(angle)) * 0.82
        break
      }
      case 'constellation': {
        const angle = Math.floor(index / 11) * ((Math.PI * 2) / 3) - Math.PI / 2
        const radius = 37 + (index % 11) * 2.1
        x = 200 + Math.cos(angle) * 68 + Math.cos(a) * radius
        y = 122 + Math.sin(angle) * 55 + Math.sin(a) * radius * 0.68
        break
      }
      case 'weave': {
        const branch = Math.floor(index / 11) - 1
        x = 28 + t * 344
        y =
          120 +
          branch * 70 * Math.cos(t * Math.PI) +
          Math.sin(t * Math.PI * 2) * 18 +
          ((index % 11) - 5) * 2.5 * Math.sin(t * Math.PI)
        break
      }
      case 'pulse':
        x = 25 + t * 350
        y = 120 + Math.sin(t * Math.PI * 4 + v * 1.8) * Math.sin(t * Math.PI) * (20 + v * 52)
        break
      default:
        x = 25 + t * 350
        y =
          125 +
          Math.sin(t * 5.8 + (index < 16 ? 0 : 2.1)) * 48 +
          ((index % 16) - 7.5) * 3.1 * Math.sin(t * Math.PI) -
          t * 22
    }
    return `${step === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
  })
  return points.join(' ')
}

const kinds: MotifKind[] = ['ribbons', 'archive', 'weave', 'constellation', 'orbit', 'pulse']
const paths = Object.fromEntries(
  kinds.map((kind) => [kind, Array.from({ length: 32 }, (_, i) => strand(kind, i))]),
) as Record<MotifKind, string[]>

export default function ResearchMotif({
  kind,
  className = '',
}: {
  kind: MotifKind
  className?: string
}) {
  const id = useId().replace(/:/g, '')
  return (
    <svg
      className={`research-motif motif-${kind} ${className}`}
      viewBox="0 0 400 240"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={`${id}-thread`} x1="0" y1="0" x2="1" y2="1">
          <stop stopColor="var(--motif-mint)" stopOpacity="0.18" />
          <stop offset="0.38" stopColor="var(--motif-pearl)" stopOpacity="0.9" />
          <stop offset="0.7" stopColor="var(--motif-mint)" stopOpacity="0.65" />
          <stop offset="1" stopColor="var(--motif-teal)" stopOpacity="0.12" />
        </linearGradient>
        <radialGradient id={`${id}-glow`}>
          <stop stopColor="var(--motif-teal)" stopOpacity="0.14" />
          <stop offset="1" stopColor="var(--motif-teal)" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="200" cy="120" rx="185" ry="113" fill={`url(#${id}-glow)`} />
      {kind === 'constellation' && (
        <g stroke="var(--motif-mint)" strokeWidth="0.7">
          <path d="M200 67L259 150L141 150Z" strokeDasharray="2 6" opacity="0.4" />
          {[
            [200, 67],
            [259, 150],
            [141, 150],
          ].map(([cx, cy]) => (
            <circle key={cx} cx={cx} cy={cy} r="2.5" fill="var(--motif-pearl)" opacity="0.8" />
          ))}
        </g>
      )}
      <g className="motif-threads" stroke={`url(#${id}-thread)`} strokeWidth="0.8">
        {paths[kind].map((d, i) => (
          <path key={i} d={d} opacity={i % 5 === 0 ? 1 : 0.58} />
        ))}
        <path
          d={paths[kind][kind === 'ribbons' ? 15 : 29]}
          stroke="var(--motif-gold)"
          strokeWidth="1"
          opacity="0.75"
        />
      </g>
      <g className="motif-satellites" stroke="var(--motif-mint)" strokeWidth="0.7" opacity="0.65">
        <path d="M28 105v8m-4-4h8 M365 155v8m-4-4h8" />
        <circle cx="337" cy="56" r="2" fill="var(--motif-gold)" stroke="none" />
        <circle cx="62" cy="181" r="1.5" fill="var(--motif-mint)" stroke="none" />
      </g>
    </svg>
  )
}
