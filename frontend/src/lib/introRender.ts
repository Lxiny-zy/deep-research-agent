/**
 * 欢迎画面的扉页插图：一张文献引用网络。
 *
 * 节点是文献，连线是引用；节点按先后依次出现——实心是已核验的来源，
 * 空心是尚待核验的线索。所有已核验的节点最后各引一条细线汇向「进入」，
 * 像一篇论文把散落的证据收束成一个结论。宽屏时插图位于标题右侧，不压文字。
 */

/** 宽屏扉页左文右图的断点，与 intro.css 的 min-width: 980px 对齐。 */
export const INTRO_WIDE = 980

/**
 * 「进入」按钮中心（CSS 像素），网络离场时向这里收拢，与 intro.css 对齐：
 * 窄屏居中于 80% 高度；宽屏随文案左对齐，落在 62% 高度、左边距 max(6vw, 48px) 处
 * （按钮最小宽 196px，中心再右移 98px）。
 */
export function introFocus(W: number, H: number): { x: number; y: number } {
  if (W >= INTRO_WIDE) return { x: Math.max(W * 0.06, 48) + 98, y: H * 0.62 }
  return { x: W / 2, y: H * 0.8 }
}

export interface IntroNode {
  /** 归一化坐标（相对插图区域） */
  x: number
  y: number
  /** 出现时刻（毫秒，相对画面出现） */
  delay: number
  verified: boolean
  /** 节点半径（CSS 像素） */
  r: number
}

export interface IntroGraph {
  nodes: IntroNode[]
  /** 引用边：[from, to] 节点下标 */
  edges: [number, number][]
}

/** 确定性生成的引用网络，每次打开画面一致。 */
export function introGraph(count = 34): IntroGraph {
  let seed = 20261112
  const random = () => {
    seed = (seed * 16807) % 2147483647
    return (seed - 1) / 2147483646
  }
  const nodes: IntroNode[] = []
  // 拒绝采样：节点之间保持最小间距，构图疏朗
  let guard = 0
  while (nodes.length < count && guard < 4000) {
    guard += 1
    const angle = random() * Math.PI * 2
    const radius = Math.sqrt(random()) * 0.5
    const x = 0.5 + Math.cos(angle) * radius
    const y = 0.5 + Math.sin(angle) * radius * 0.86
    if (nodes.some((n) => Math.hypot(n.x - x, n.y - y) < 0.085)) continue
    const centrality = 1 - radius * 2
    nodes.push({
      x,
      y,
      delay: 200 + (1 - centrality) * 1400 + random() * 500,
      verified: random() < 0.38 + centrality * 0.5,
      r: 1.8 + centrality * 2.4 + random() * 0.8,
    })
  }
  const edges: [number, number][] = []
  nodes.forEach((a, i) => {
    const near = nodes
      .map((b, j) => ({ j, d: Math.hypot(a.x - b.x, a.y - b.y) }))
      .filter(({ j }) => j !== i)
      .sort((p, q) => p.d - q.d)
      .slice(0, 2 + Math.floor(random() * 2))
    for (const { j, d } of near) {
      if (d > 0.26) continue
      if (edges.some(([p, q]) => (p === i && q === j) || (p === j && q === i))) continue
      edges.push([i, j])
    }
  })
  return { nodes, edges }
}

export interface IntroFrame {
  width: number
  height: number
  elapsed: number
  /** 指针悬停「进入」的程度 0..1：汇聚线加深 */
  energy: number
  /** 离场收拢进度 0..1 */
  gather: number
  dark: boolean
  graph: IntroGraph
}

type RGB = [number, number, number]

const rgba = ([r, g, b]: RGB, a: number) =>
  `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, a)).toFixed(3)})`
const ease = (t: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3)

/** 与 tokens.css 的 --text / --accent 一致：墨色是文献，期刊红是已核验。 */
function palette(dark: boolean): { ink: RGB; accent: RGB } {
  return dark
    ? { ink: [226, 222, 212], accent: [224, 122, 102] }
    : { ink: [28, 26, 23], accent: [158, 42, 30] }
}

/** 插图区域：宽屏时在右半边，窄屏时退到标题后方的淡底。 */
function plate(W: number, H: number) {
  if (W >= INTRO_WIDE) {
    const size = Math.min(W * 0.36, H * 0.6)
    return { cx: W * 0.74, cy: H * 0.43, size, faint: 1 }
  }
  const size = Math.min(W * 0.9, H * 0.5)
  return { cx: W * 0.5, cy: H * 0.42, size, faint: 0.3 }
}

export function drawIntro(ctx: CanvasRenderingContext2D, f: IntroFrame) {
  const { width: W, height: H, dark, energy, gather, graph } = f
  ctx.clearRect(0, 0, W, H)
  if (W <= 0 || H <= 0) return

  const { ink, accent } = palette(dark)
  const { cx, cy, size, faint } = plate(W, H)
  const keep = 1 - gather
  const focus = introFocus(W, H)
  const fx = focus.x
  const fy = focus.y - 24
  const pos = graph.nodes.map((n) => {
    const x = cx + (n.x - 0.5) * size
    const y = cy + (n.y - 0.5) * size
    return { x: x + (fx - x) * gather, y: y + (fy - y) * gather }
  })
  const lit = graph.nodes.map((n) => ease((f.elapsed - n.delay) / 700))

  // 1. 引用边：从一端向另一端生长；两端都已核验的边用期刊红
  ctx.lineWidth = 0.8
  for (const [i, j] of graph.edges) {
    const t = Math.min(lit[i], lit[j])
    if (t <= 0) continue
    const both = graph.nodes[i].verified && graph.nodes[j].verified
    ctx.strokeStyle = both
      ? rgba(accent, (dark ? 0.42 : 0.34) * t * keep * faint)
      : rgba(ink, (dark ? 0.16 : 0.13) * t * keep * faint)
    ctx.beginPath()
    ctx.moveTo(pos[i].x, pos[i].y)
    ctx.lineTo(pos[i].x + (pos[j].x - pos[i].x) * t, pos[i].y + (pos[j].y - pos[i].y) * t)
    ctx.stroke()
  }

  // 2. 汇聚线：已核验的节点各引一条细线到「进入」上沿（仅宽屏构图）
  if (faint === 1) {
    const converge = ease((f.elapsed - 2000) / 1200)
    if (converge > 0) {
      ctx.lineWidth = 0.6
      graph.nodes.forEach((n, i) => {
        if (!n.verified || lit[i] < 1) return
        const a = (dark ? 0.1 : 0.08) + energy * (dark ? 0.22 : 0.16)
        ctx.strokeStyle = rgba(accent, a * converge * keep)
        const { x, y } = pos[i]
        const ex = fx + (x - cx) * 0.06
        ctx.beginPath()
        ctx.moveTo(x, y)
        ctx.bezierCurveTo(x, y + (fy - y) * 0.55, ex, fy - (fy - y) * 0.25, ex, fy)
        ctx.stroke()
      })
    }
  }

  // 3. 节点：已核验实心期刊红，待核验空心墨圈
  graph.nodes.forEach((n, i) => {
    const t = lit[i]
    if (t <= 0) return
    const { x, y } = pos[i]
    const r = n.r * (0.6 + 0.4 * t) * (1 - gather * 0.5)
    ctx.beginPath()
    ctx.arc(x, y, r, 0, Math.PI * 2)
    if (n.verified) {
      ctx.fillStyle = rgba(accent, (dark ? 0.9 : 0.85) * t * faint)
      ctx.fill()
    } else {
      ctx.lineWidth = 1
      ctx.strokeStyle = rgba(ink, (dark ? 0.5 : 0.42) * t * faint)
      ctx.stroke()
    }
  })
}
