/**
 * 谱线：全站视觉与文案共用的一个隐喻。
 *
 * 每种元素都把自己的身份写进光里——一组位置固定、无法伪造的谱线。
 * 我们把它读成两件事：
 * - 命运：物质的来处早已写在光中，只待有人读出；
 * - 思维：一个问题拆成许多条思路，有证据处亮起，缺口处留下暗线，最后汇成一谱。
 *
 * 每个页面认领一组真实谱线（波长取实验室 / 太阳光谱的常用值，单位 nm）。
 * 背景画布、侧栏色标与顶栏标注都从这里取值，不再各页各堆一套装饰。
 */

export type RGB = [number, number, number]

export interface SpectralLine {
  /** 谱线记号，如 Hα、Na D₂ */
  symbol: string
  nm: number
  /** 相对强度 0..1；吸收线用于在光谱带上刻出暗线 */
  strength: number
  kind?: 'emission' | 'absorption'
}

export interface PageSpectrum {
  /** 元素名（中文） */
  element: string
  /** 页面认领的那一条谱线：顶栏标注与侧栏色标 */
  line: SpectralLine
  /** 背景里出现的全部谱线（含 line 本身） */
  lines: SpectralLine[]
  /** 这条谱线为何属于这一页：一句话，供提示与无障碍说明 */
  note: string
}

export const VISIBLE_MIN = 380
export const VISIBLE_MAX = 780

const em = (symbol: string, nm: number, strength: number): SpectralLine => ({
  symbol,
  nm,
  strength,
})

/** 地球大气的氧吸收带：无论看向哪里，我们都隔着自己的大气读谱。 */
export const TELLURIC: SpectralLine[] = [
  { symbol: 'O₂ B', nm: 686.7, strength: 0.6, kind: 'absorption' },
  { symbol: 'O₂ A', nm: 759.4, strength: 0.8, kind: 'absorption' },
]

const H_ALPHA = em('Hα', 656.3, 1)
const NA_D2 = em('Na D₂', 589.0, 1)
const FE_E = em('Fe I', 527.0, 0.9)
const CA_K = em('Ca II K', 393.4, 1)
const HE_D3 = em('He I D₃', 587.6, 1)
const O3 = em('[O III]', 500.7, 1)
const NE = em('Ne I', 640.2, 1)
const MG_B = em('Mg b₁', 518.4, 1)
const HG = em('Hg I', 435.8, 1)

export const PAGE_SPECTRA = {
  home: {
    element: '氢',
    line: H_ALPHA,
    lines: [H_ALPHA, em('Hβ', 486.1, 0.7), em('Hγ', 434.0, 0.5), em('Hδ', 410.2, 0.38)],
    note: '氢是宇宙最早的元素，一切谱线由它开始。',
  },
  qa: {
    element: '钠',
    line: NA_D2,
    lines: [NA_D2, em('Na D₁', 589.6, 0.82), em('Na', 568.8, 0.3), em('Na', 498.3, 0.22)],
    note: '钠的 D 双线并肩而立，像一问一答。',
  },
  history: {
    element: '铁',
    line: FE_E,
    lines: [
      FE_E,
      em('Fe I', 404.6, 0.4),
      em('Fe I', 438.4, 0.55),
      em('Fe I', 495.8, 0.45),
      em('Fe I', 532.8, 0.5),
      em('Fe I', 561.6, 0.3),
    ],
    note: '铁是恒星燃烧的终点，走过的路都沉积在这里。',
  },
  library: {
    element: '钙',
    line: CA_K,
    lines: [CA_K, em('Ca II H', 396.8, 0.92), em('Ca I', 422.7, 0.5), em('Ca II', 616.2, 0.3)],
    note: '钙是骨骼的材料，资料是研究的骨架。',
  },
  run: {
    element: '氦',
    line: HE_D3,
    lines: [
      HE_D3,
      em('He I', 447.1, 0.55),
      em('He I', 492.2, 0.3),
      em('He I', 501.6, 0.5),
      em('He I', 667.8, 0.6),
      em('He I', 706.5, 0.4),
    ],
    note: '氦先在日光的谱线里被看见，二十七年后才在地上被找到。',
  },
  reader: {
    element: '氧',
    line: O3,
    lines: [O3, em('[O III]', 495.9, 0.45), em('Hβ', 486.1, 0.35), em('[O III]', 436.3, 0.2)],
    note: '这条禁线曾被误认作新元素「星云素」，细读才不被表象误导。',
  },
  workflows: {
    element: '氖',
    line: NE,
    lines: [
      NE,
      em('Ne I', 585.2, 0.7),
      em('Ne I', 614.3, 0.6),
      em('Ne I', 650.7, 0.55),
      em('Ne I', 692.9, 0.45),
      em('Ne I', 703.2, 0.5),
    ],
    note: '氖灯的红线一条接一条亮起，像依次运行的工序。',
  },
  agents: {
    element: '镁',
    line: MG_B,
    lines: [MG_B, em('Mg b₂', 517.3, 0.85), em('Mg b₄', 516.7, 0.7), em('Mg I', 457.1, 0.3)],
    note: '镁 b 三线挨得很近，各司其职，又彼此成组。',
  },
  tools: {
    element: '汞',
    line: HG,
    lines: [
      HG,
      em('Hg I', 404.7, 0.6),
      em('Hg I', 546.1, 0.9),
      em('Hg I', 577.0, 0.5),
      em('Hg I', 579.1, 0.5),
    ],
    note: '汞灯是光谱仪的校准光源：先校准，再读谱。',
  },
} satisfies Record<string, PageSpectrum>

export type SpectrumKey = keyof typeof PAGE_SPECTRA

export function spectrumKey(pathname: string): SpectrumKey {
  if (pathname === '/') return 'home'
  if (pathname.startsWith('/qa')) return 'qa'
  if (pathname.startsWith('/history')) return 'history'
  if (pathname.startsWith('/library')) return 'library'
  if (/^\/runs\/[^/]+\/read/.test(pathname)) return 'reader'
  if (pathname.startsWith('/runs/')) return 'run'
  if (pathname.startsWith('/workflows')) return 'workflows'
  if (pathname.startsWith('/agents')) return 'agents'
  return 'tools'
}

/** 顶栏 / 侧栏用的简短标注，如「Hα 656.3 nm」。 */
export function lineLabel(line: SpectralLine): string {
  return `${line.symbol} ${line.nm.toFixed(1)} nm`
}

/**
 * 可见光波长 → 近似 sRGB（经典分段近似，两端按人眼灵敏度衰减）。
 * 输出做了最低亮度托底，保证暗色背景上的紫端与红端谱线仍然看得见。
 */
export function wavelengthToRgb(nm: number): RGB {
  const w = Math.min(VISIBLE_MAX, Math.max(VISIBLE_MIN, nm))
  let r = 0
  let g = 0
  let b = 0
  if (w < 440) {
    r = -(w - 440) / (440 - 380)
    b = 1
  } else if (w < 490) {
    g = (w - 440) / (490 - 440)
    b = 1
  } else if (w < 510) {
    g = 1
    b = -(w - 510) / (510 - 490)
  } else if (w < 580) {
    r = (w - 510) / (580 - 510)
    g = 1
  } else if (w < 645) {
    r = 1
    g = -(w - 645) / (645 - 580)
  } else {
    r = 1
  }
  const edge =
    w < 420 ? 0.45 + (0.55 * (w - 380)) / 40 : w > 700 ? 0.45 + (0.55 * (780 - w)) / 80 : 1
  const channel = (value: number) => Math.round(255 * Math.max(0.16, Math.pow(value * edge, 0.8)))
  return [channel(r), channel(g), channel(b)]
}

export function rgbCss([r, g, b]: RGB, alpha = 1): string {
  return alpha >= 1 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`
}

/** 谱线在 [0, 1] 横轴上的位置（380 nm 在左，780 nm 在右）。 */
export function lineX(nm: number): number {
  return (nm - VISIBLE_MIN) / (VISIBLE_MAX - VISIBLE_MIN)
}

export const MAX_LINES = 6

/** 页面切换时逐帧插值的谱线状态；缺的谱线强度为 0，从无到有地「显影」。 */
export interface LiveLine {
  nm: number
  strength: number
}

export function padLines(lines: SpectralLine[]): LiveLine[] {
  const emission = lines.filter((line) => line.kind !== 'absorption').slice(0, MAX_LINES)
  const out: LiveLine[] = emission.map((line) => ({ nm: line.nm, strength: line.strength }))
  const anchor = out[out.length - 1] ?? { nm: 560, strength: 0 }
  while (out.length < MAX_LINES) out.push({ nm: anchor.nm, strength: 0 })
  return out
}

/** 向目标插值一步；返回是否仍在变化。 */
export function stepLines(current: LiveLine[], target: LiveLine[], rate: number): boolean {
  let moving = false
  current.forEach((line, index) => {
    const goal = target[index]
    const next = (a: number, b: number, eps: number) => {
      const v = a + (b - a) * rate
      if (Math.abs(b - v) > eps) moving = true
      return v
    }
    line.nm = next(line.nm, goal.nm, 0.01)
    line.strength = next(line.strength, goal.strength, 1e-4)
  })
  return moving
}

export function gaussian(x: number, center: number, width: number): number {
  const d = (x - center) / width
  return Math.exp(-d * d)
}
