/**
 * 谱线：全站视觉与文案共用的一个隐喻——一问一谱。
 *
 * 一个问题拆成许多条思路；有证据处亮起，缺口处留下暗线，最后汇成一谱。
 * 亮暗只由真实的证据决定，无从伪造：任务页和任务记录里的谱纹来自子问题与
 * 逐字核验结果（见 thoughtLines）。
 *
 * 每个页面是研究流程中的一步，在光谱上占一个位置：从「起问」到「校准」由紫到红排开，
 * 侧栏从上到下读下来就是一段完整的光谱。背景、侧栏色标与顶栏都从这里取值。
 */

export type RGB = [number, number, number]

export interface SpectralLine {
  nm: number
  /** 相对强度 0..1；暗线用于在光谱带上刻出暗缝 */
  strength: number
  kind?: 'emission' | 'absorption'
}

export interface PageSpectrum {
  /** 这一页在研究流程中的一步：起问、追问…… */
  stage: string
  /** 这一步在光谱上的位置（nm） */
  nm: number
  /** 这一步在研究里做什么：一句话，供提示与无障碍说明 */
  note: string
}

export const VISIBLE_MIN = 380
export const VISIBLE_MAX = 780

/** 暗线：未解之问。背景与欢迎画面的光谱带上刻出的几道暗缝。 */
export const DARK_LINES: SpectralLine[] = [
  { nm: 452, strength: 0.5, kind: 'absorption' },
  { nm: 553, strength: 0.6, kind: 'absorption' },
  { nm: 688, strength: 0.75, kind: 'absorption' },
]

export const PAGE_SPECTRA = {
  home: { stage: '起问', nm: 425, note: '从一个问题出发，把它拆成可以取证的思路。' },
  qa: { stage: '追问', nm: 462, note: '把疑点问到底，每个回答都标出它的来处。' },
  run: { stage: '取证', nm: 500, note: '思路逐条检索、逐字核验：有证据的亮起，没有的留暗。' },
  reader: { stage: '精读', nm: 530, note: '对照原文逐字读，结论回到原句。' },
  history: { stage: '回溯', nm: 560, note: '回看做过的研究；当初的暗线，也许已能点亮。' },
  library: { stage: '积证', nm: 585, note: '把文献与资料收在一处，等问题来时作证。' },
  workflows: { stage: '次第', nm: 612, note: '安排研究的先后：检索、核验、补洞、成文。' },
  agents: { stage: '分工', nm: 640, note: '不同角色各执一线，合成一份研究。' },
  tools: { stage: '校准', nm: 668, note: '先校准模型、检索与质量门，再开始研究。' },
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

/** 页面切换时逐帧插值的谱线状态。 */
export interface LiveLine {
  nm: number
  strength: number
}

/** 本页在背景光谱带上的那一条线。 */
export function pageLines(key: SpectrumKey): LiveLine[] {
  return [{ nm: PAGE_SPECTRA[key].nm, strength: 1 }]
}

/** 向目标插值一步；返回是否仍在变化。 */
export function stepLines(current: LiveLine[], target: LiveLine[], rate: number): boolean {
  let moving = false
  current.forEach((line, index) => {
    const goal = target[index]
    if (!goal) return
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
