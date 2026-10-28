import {
  gaussian,
  lineX,
  PAGE_SPECTRA,
  rgbCss,
  TELLURIC,
  VISIBLE_MAX,
  VISIBLE_MIN,
  wavelengthToRgb,
  type RGB,
} from './spectralLines'

/** 欢迎画面的几何：光谱带与汇聚点的高度（画布高度的比例），与 intro.css 的版式对齐。 */
export const INTRO_BAND = 0.5
export const INTRO_FOCUS = 0.8

export interface IntroThread {
  nm: number
  strength: number
  /** 显影延迟（毫秒） */
  delay: number
  /** 氦 D₃：唯一带标注的那一条 */
  key: boolean
}

/**
 * 欢迎画面上的全部谱线：各页认领的元素谱线合在一起，按波长排好。
 * 相距不足 1 nm 的谱线合并为一条，避免在屏幕上叠成一团。
 */
export function introThreads(): IntroThread[] {
  const all = Object.values(PAGE_SPECTRA)
    .flatMap((page) => page.lines)
    .sort((a, b) => a.nm - b.nm)
  const merged: { nm: number; strength: number }[] = []
  for (const line of all) {
    const last = merged[merged.length - 1]
    if (last && line.nm - last.nm < 1) last.strength = Math.max(last.strength, line.strength)
    else merged.push({ nm: line.nm, strength: line.strength })
  }
  return merged.map((line, index) => ({
    ...line,
    delay: 240 + ((index * 37) % 23) * 62,
    key: Math.abs(line.nm - 587.6) < 0.05,
  }))
}

export interface IntroFrame {
  width: number
  height: number
  time: number
  /** 画面出现后经过的毫秒数，用于谱线依次显影 */
  elapsed: number
  /** 指针悬停「进入」时为 1 */
  energy: number
  /** 离场时由 0 走到 1：谱线与光谱带一起收拢到汇聚点 */
  gather: number
  pointer: { x: number; y: number; s: number }
  dark: boolean
  threads: IntroThread[]
}

const ease = (t: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3)

const tone = (rgb: RGB, dark: boolean): RGB =>
  dark ? rgb : ([rgb[0] * 0.7, rgb[1] * 0.64, rgb[2] * 0.76].map(Math.round) as RGB)

/**
 * 画一帧欢迎画面：
 * 光谱带以上，谱线笔直垂下——位置由元素决定，像早已写定的命运；
 * 光谱带以下，所有谱线弯向同一个汇聚点——许多条思路，最终汇成一个回答。
 * 离场时整幅画面收拢进汇聚点，随后由 intro.css 从这一点打开通往主界面的圆孔。
 */
export function drawIntro(ctx: CanvasRenderingContext2D, f: IntroFrame) {
  const { width: W, height: H, time, pointer, dark, energy, gather } = f
  ctx.clearRect(0, 0, W, H)
  if (W <= 0 || H <= 0) return
  const left = W * 0.08
  const right = W * 0.92
  const bandY = H * INTRO_BAND
  const fx = W / 2
  const fy = H * INTRO_FOCUS
  const xOf = (nm: number) => left + lineX(nm) * (right - left)
  const bandIn = ease(f.elapsed / 900) * (1 - gather)

  ctx.globalCompositeOperation = dark ? 'lighter' : 'source-over'

  // 光谱带：自中心向两侧展开；离场时向中心收拢
  const spectrum = (alpha: number) => {
    const grad = ctx.createLinearGradient(left, 0, right, 0)
    for (let i = 0; i <= 12; i++) {
      const t = i / 12
      const nm = VISIBLE_MIN + t * (VISIBLE_MAX - VISIBLE_MIN)
      const fade = Math.min(1, t * 5, (1 - t) * 5)
      grad.addColorStop(t, rgbCss(tone(wavelengthToRgb(nm), dark), alpha * fade))
    }
    return grad
  }
  const half = ((right - left) / 2) * bandIn
  ctx.lineCap = 'round'
  for (const [lineWidth, alpha] of [
    [30, dark ? 0.06 : 0.05],
    [8, dark ? 0.16 : 0.1],
    [1.2, dark ? 0.75 : 0.55],
  ] as const) {
    ctx.strokeStyle = spectrum(alpha * (1 - gather))
    ctx.lineWidth = lineWidth
    ctx.beginPath()
    ctx.moveTo(fx - half, bandY)
    ctx.lineTo(fx + half, bandY)
    ctx.stroke()
  }

  // 吸收线：大气在光谱带上留下的暗缝
  ctx.globalCompositeOperation = 'destination-out'
  for (const line of TELLURIC) {
    ctx.fillStyle = `rgba(0, 0, 0, ${(0.8 * line.strength * bandIn).toFixed(3)})`
    ctx.fillRect(xOf(line.nm) - 1.5, bandY - 16, 3, 32)
  }
  ctx.globalCompositeOperation = dark ? 'lighter' : 'source-over'

  // 汇聚点的光晕：悬停时变亮，离场时收成一颗明亮的光核
  const focusAlpha = Math.min(
    0.9,
    (dark ? 0.12 : 0.08) + energy * 0.16 + gather * (dark ? 0.7 : 0.25),
  )
  const focusR = 70 + energy * 40 - gather * 20
  const glow = (alpha: number) =>
    dark ? `rgba(255, 246, 228, ${alpha.toFixed(3)})` : `rgba(120, 96, 60, ${alpha.toFixed(3)})`
  const focus = ctx.createRadialGradient(fx, fy, 0, fx, fy, focusR)
  focus.addColorStop(0, glow(focusAlpha))
  focus.addColorStop(0.3, glow(focusAlpha * 0.3))
  focus.addColorStop(1, glow(0))
  ctx.fillStyle = focus
  ctx.fillRect(fx - focusR, fy - focusR, focusR * 2, focusR * 2)

  f.threads.forEach((thread, index) => {
    const appear = ease((f.elapsed - thread.delay) / 1100)
    if (appear <= 0) return
    const x0 = xOf(thread.nm)
    const rgb = tone(wavelengthToRgb(thread.nm), dark)
    const near = pointer.s * gaussian(x0, pointer.x, 70) * gaussian(pointer.y, bandY * 0.6, H * 0.4)
    const alpha = Math.min(
      1,
      ((dark ? 0.2 : 0.16) + (dark ? 0.5 : 0.42) * thread.strength) *
        appear *
        (thread.key ? 1.35 : 1) +
        near * 0.45,
    )

    // 上段：从光谱带向上生长，越高越淡，轻微摆动；离场时缩回光谱带
    const top = bandY - bandY * appear * (1 - gather)
    const up = ctx.createLinearGradient(0, top, 0, bandY)
    up.addColorStop(0, rgbCss(rgb, 0))
    up.addColorStop(1, rgbCss(rgb, alpha))
    ctx.strokeStyle = up
    ctx.lineWidth = thread.key ? 1.4 : 1
    ctx.beginPath()
    for (let y = bandY; y >= top; y -= 8) {
      const reach = (bandY - y) / Math.max(1, bandY)
      const sway = 4 * reach * Math.sin(y * 0.012 + time * 0.0006 + index * 1.7)
      const bend = near * 10 * reach * Math.sign(pointer.x - x0)
      if (y === bandY) ctx.moveTo(x0, y)
      else ctx.lineTo(x0 + sway + bend, y)
    }
    ctx.stroke()

    // 下段：沿二次曲线弯向汇聚点；悬停「进入」时收得更紧、更亮；离场时从光谱带一端缩进汇聚点
    const cy = bandY + (fy - bandY) * (0.62 - energy * 0.18)
    const down = ctx.createLinearGradient(0, bandY, 0, fy)
    down.addColorStop(0, rgbCss(rgb, alpha * 0.9))
    down.addColorStop(1, rgbCss(rgb, alpha * (0.2 + energy * 0.5)))
    ctx.strokeStyle = down
    ctx.lineWidth = thread.key ? 1.2 : 0.8
    ctx.beginPath()
    const steps = 28
    const from = Math.min(steps, Math.ceil(steps * gather))
    for (let i = from; i <= steps * appear; i++) {
      const t = i / steps
      const x = (1 - t) * (1 - t) * x0 + 2 * (1 - t) * t * x0 + t * t * fx
      const y = (1 - t) * (1 - t) * bandY + 2 * (1 - t) * t * cy + t * t * fy
      if (i === from) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.stroke()

    // 交点光斑
    const r = 3 + 5 * thread.strength
    const dot = ctx.createRadialGradient(x0, bandY, 0, x0, bandY, r)
    dot.addColorStop(0, rgbCss(rgb, Math.min(1, alpha * 1.5) * (1 - gather)))
    dot.addColorStop(1, rgbCss(rgb, 0))
    ctx.fillStyle = dot
    ctx.fillRect(x0 - r, bandY - r, r * 2, r * 2)

    if (thread.key) {
      const labelIn = ease((f.elapsed - thread.delay - 900) / 900) * (1 - gather)
      if (labelIn > 0) {
        ctx.globalCompositeOperation = 'source-over'
        ctx.font = '11px ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace'
        ctx.textAlign = 'left'
        ctx.fillStyle = dark
          ? `rgba(236, 232, 220, ${(0.72 * labelIn).toFixed(3)})`
          : `rgba(48, 42, 30, ${(0.7 * labelIn).toFixed(3)})`
        // 窄屏上标注放到光谱带下方，避免压住上方的说明文字
        ctx.fillText('He I 587.6 nm · 1868', x0 + 10, W < 720 ? bandY + 24 : bandY - 14)
        ctx.globalCompositeOperation = dark ? 'lighter' : 'source-over'
      }
    }
  })
  ctx.globalCompositeOperation = 'source-over'
}
