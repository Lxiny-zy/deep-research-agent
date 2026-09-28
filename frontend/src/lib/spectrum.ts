/**
 * 光谱背景的「页面特征」与曲线计算（纯函数，便于测试）。
 *
 * 每个页面一组高斯波峰：位置 x / 宽度 w 以画布宽度为单位，高度 h 以基线高度为单位，
 * rgb 为该峰的主色。切换页面时画布在两组特征之间插值，所以光谱是「变形」而不是跳变。
 */

export type RGB = [number, number, number]

export interface Peak {
  x: number
  w: number
  h: number
  rgb: RGB
}

export interface Signature {
  /** 基线位置：画布高度的比例 */
  baseline: number
  peaks: Peak[]
}

export const MAX_PEAKS = 6

const BLUE: RGB = [111, 147, 240]
const VIOLET: RGB = [164, 124, 240]
const PINK: RGB = [240, 139, 180]
const AMBER: RGB = [243, 176, 79]
const MINT: RGB = [111, 207, 174]

const peak = (x: number, w: number, h: number, rgb: RGB): Peak => ({ x, w, h, rgb })

/** 卡片型页面的基线：页面中下部，峰体落在卡片后面 */
const PANEL_BASELINE = 0.72

export const SIGNATURES: Record<string, Signature> = {
  // 工作台：三座主峰，偏右上，与标题区错开
  home: {
    baseline: 0.44,
    peaks: [
      peak(0.55, 0.05, 0.6, BLUE),
      peak(0.68, 0.055, 0.74, VIOLET),
      peak(0.82, 0.06, 0.66, AMBER),
    ],
  },
  // 学术问答：页面由整块对话面板占满，光谱改为从底边升起，透过毛玻璃可见
  qa: {
    baseline: 0.98,
    peaks: [peak(0.6, 0.07, 0.42, MINT), peak(0.8, 0.05, 0.58, PINK)],
  },
  // 以下各页由卡片占满：光谱基线放在页面中下部（PANEL_BASELINE），
  // 峰体压在卡片后面、透过玻璃以柔和色晕可见，而不是挤在页头一小条里被截断。
  // 任务记录：低矮连绵的连续谱，像时间轴
  history: {
    baseline: PANEL_BASELINE,
    peaks: [
      peak(0.42, 0.08, 0.2, BLUE),
      peak(0.56, 0.08, 0.25, VIOLET),
      peak(0.7, 0.08, 0.22, PINK),
      peak(0.84, 0.08, 0.26, AMBER),
    ],
  },
  // 资料库：密集的窄峰，像一排书脊 / 离散谱线
  library: {
    baseline: PANEL_BASELINE,
    peaks: [
      peak(0.5, 0.022, 0.36, BLUE),
      peak(0.58, 0.024, 0.52, VIOLET),
      peak(0.66, 0.022, 0.33, PINK),
      peak(0.74, 0.026, 0.59, AMBER),
      peak(0.82, 0.022, 0.39, MINT),
      peak(0.9, 0.024, 0.29, BLUE),
    ],
  },
  // 任务详情：四峰，中等高度，给正文留出空间
  run: {
    baseline: PANEL_BASELINE,
    peaks: [
      peak(0.5, 0.048, 0.29, VIOLET),
      peak(0.62, 0.054, 0.39, BLUE),
      peak(0.76, 0.048, 0.33, MINT),
      peak(0.88, 0.048, 0.36, AMBER),
    ],
  },
  // 工作流构建：等宽峰逐级升高，像流水线的一道道工序
  workflows: {
    baseline: PANEL_BASELINE,
    peaks: [
      peak(0.5, 0.034, 0.18, MINT),
      peak(0.6, 0.034, 0.29, BLUE),
      peak(0.7, 0.034, 0.39, VIOLET),
      peak(0.8, 0.034, 0.49, PINK),
      peak(0.9, 0.034, 0.6, AMBER),
    ],
  },
  // 角色广场：一对并肩的高峰（两个角色对话），脚下铺一层宽而低的底谱
  agents: {
    baseline: PANEL_BASELINE,
    peaks: [
      peak(0.72, 0.17, 0.17, MINT),
      peak(0.66, 0.038, 0.56, PINK),
      peak(0.77, 0.038, 0.46, AMBER),
    ],
  },
  // 设置：单一主峰，最安静的一页
  tools: {
    baseline: PANEL_BASELINE,
    peaks: [peak(0.78, 0.07, 0.49, VIOLET)],
  },
}

export function signatureKey(pathname: string): keyof typeof SIGNATURES {
  if (pathname === '/') return 'home'
  if (pathname.startsWith('/qa')) return 'qa'
  if (pathname.startsWith('/history')) return 'history'
  if (pathname.startsWith('/library')) return 'library'
  if (pathname.startsWith('/runs/')) return 'run'
  if (pathname.startsWith('/workflows')) return 'workflows'
  if (pathname.startsWith('/agents')) return 'agents'
  return 'tools'
}

/** 补齐到 MAX_PEAKS：缺的峰高度为 0、贴在最后一个峰旁，插值时从无到有地「长出来」。 */
export function padPeaks(peaks: Peak[]): Peak[] {
  const last = peaks[peaks.length - 1] ?? peak(0.8, 0.05, 0, VIOLET)
  const padded = peaks.slice(0, MAX_PEAKS)
  while (padded.length < MAX_PEAKS) padded.push({ ...last, h: 0 })
  return padded
}

/** 单峰高斯轮廓（0..1）。 */
export function gaussian(x: number, center: number, width: number): number {
  const d = (x - center) / width
  return Math.exp(-d * d)
}

/** 向目标插值一步；返回是否仍在变化（用于判断静止帧）。 */
export function stepToward(current: Peak[], target: Peak[], rate: number): boolean {
  let moving = false
  current.forEach((p, i) => {
    const t = target[i]
    const next = (a: number, b: number) => {
      const v = a + (b - a) * rate
      if (Math.abs(b - v) > 1e-4) moving = true
      return v
    }
    p.x = next(p.x, t.x)
    p.w = next(p.w, t.w)
    p.h = next(p.h, t.h)
    p.rgb = [next(p.rgb[0], t.rgb[0]), next(p.rgb[1], t.rgb[1]), next(p.rgb[2], t.rgb[2])]
  })
  return moving
}
