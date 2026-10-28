import {
  gaussian,
  lineX,
  rgbCss,
  VISIBLE_MAX,
  VISIBLE_MIN,
  wavelengthToRgb,
  type LiveLine,
  type RGB,
  type SpectralLine,
} from './spectralLines'

/** 一帧的绘制输入。坐标均为 CSS 像素。 */
export interface Frame {
  width: number
  height: number
  /** 光谱带所在高度：画布高度的比例（贴近视口底边） */
  band: number
  /** 发射谱线（页面切换时插值中的状态） */
  lines: LiveLine[]
  /** 吸收线：在光谱带上刻出的暗缝 */
  absorption: SpectralLine[]
  time: number
  /** 指针位置与影响强度（0 = 指针不在画面内） */
  pointer: { x: number; y: number; s: number }
  dark: boolean
}

const STEP = 6

/** 光谱带在画布上的横向范围：避开左侧导航，右侧留一点边。 */
export function bandSpan(width: number): [number, number] {
  const left = Math.min(width * 0.5, Math.max(width * 0.22, 260))
  return [left, width - 24]
}

const tone = (rgb: RGB, dark: boolean): RGB =>
  dark ? rgb : ([rgb[0] * 0.72, rgb[1] * 0.68, rgb[2] * 0.78].map(Math.round) as RGB)

/**
 * 画出谱线背景：贴着视口底边的一道连续光谱，像地平线上的光；本页的谱线从中升起一小段，
 * 越高越淡，不穿过阅读区。指针靠近时，附近的谱线升得更高、更亮。吸收线在光谱带上留下暗缝。
 */
export function drawSpectralLines(ctx: CanvasRenderingContext2D, f: Frame) {
  const { width: W, height: H, dark, time, pointer } = f
  const [left, right] = bandSpan(W)
  const span = right - left
  const bandY = f.band * H
  const xOf = (nm: number) => left + lineX(nm) * span

  ctx.clearRect(0, 0, W, H)
  if (W <= 0 || H <= 0 || span <= 0) return

  // 连续光谱带：三层描边叠出光晕，两端渐隐
  const spectrum = (alpha: number) => {
    const grad = ctx.createLinearGradient(left, 0, right, 0)
    for (let i = 0; i <= 10; i++) {
      const t = i / 10
      const nm = VISIBLE_MIN + t * (VISIBLE_MAX - VISIBLE_MIN)
      const fade = Math.min(1, t * 6, (1 - t) * 6)
      grad.addColorStop(t, rgbCss(tone(wavelengthToRgb(nm), dark), alpha * fade))
    }
    return grad
  }
  ctx.globalCompositeOperation = dark ? 'lighter' : 'source-over'
  for (const [lineWidth, alpha] of [
    [26, dark ? 0.05 : 0.035],
    [6, dark ? 0.12 : 0.07],
    [1, dark ? 0.55 : 0.4],
  ] as const) {
    ctx.strokeStyle = spectrum(alpha)
    ctx.lineWidth = lineWidth
    ctx.beginPath()
    ctx.moveTo(left, bandY)
    ctx.lineTo(right, bandY)
    ctx.stroke()
  }

  // 吸收线：在光谱带上刻出暗缝
  ctx.globalCompositeOperation = 'destination-out'
  for (const line of f.absorption) {
    const x = xOf(line.nm)
    ctx.fillStyle = `rgba(0, 0, 0, ${(0.85 * line.strength).toFixed(3)})`
    ctx.fillRect(x - 1.5, bandY - 14, 3, 28)
  }

  // 发射谱线：从光谱带向上升起一小段，顶端轻轻摆动
  ctx.globalCompositeOperation = dark ? 'lighter' : 'source-over'
  f.lines.forEach((line, index) => {
    if (line.strength < 0.01) return
    const x0 = xOf(line.nm)
    const rgb = tone(wavelengthToRgb(line.nm), dark)
    const near = pointer.s * gaussian(x0, pointer.x, 90) * gaussian(pointer.y, bandY, H * 0.3)
    const alpha = Math.min(1, line.strength * (dark ? 0.55 : 0.45) + near * 0.35)
    const rise = Math.min(bandY, H * (0.08 + 0.14 * line.strength) * (1 + near * 0.6))
    const top = bandY - rise
    const grad = ctx.createLinearGradient(0, top, 0, bandY)
    grad.addColorStop(0, rgbCss(rgb, 0))
    grad.addColorStop(1, rgbCss(rgb, alpha))
    ctx.strokeStyle = grad
    ctx.lineWidth = 1.1
    ctx.beginPath()
    for (let y = bandY; y >= top; y -= STEP) {
      const reach = (bandY - y) / Math.max(1, rise)
      const sway = 3 * reach * reach * Math.sin(y * 0.02 + time * 0.0005 + index * 1.3)
      if (y === bandY) ctx.moveTo(x0, y)
      else ctx.lineTo(x0 + sway, y)
    }
    ctx.stroke()

    // 谱线与光谱带的交点：一枚小光斑
    const glow = ctx.createRadialGradient(x0, bandY, 0, x0, bandY, 10)
    glow.addColorStop(0, rgbCss(rgb, Math.min(1, alpha * 1.6)))
    glow.addColorStop(1, rgbCss(rgb, 0))
    ctx.fillStyle = glow
    ctx.fillRect(x0 - 10, bandY - 10, 20, 20)
  })
  ctx.globalCompositeOperation = 'source-over'
}
