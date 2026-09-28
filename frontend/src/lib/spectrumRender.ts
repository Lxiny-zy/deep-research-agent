import { gaussian, type Peak } from './spectrum'

/** 一帧的绘制输入。坐标均为 CSS 像素。 */
export interface Frame {
  width: number
  height: number
  baseline: number
  peaks: Peak[]
  time: number
  /** 指针位置与影响强度（0 = 指针不在画面内） */
  pointer: { x: number; y: number; s: number }
  dark: boolean
}

const STRANDS = 16

const rgba = ([r, g, b]: Peak['rgb'], a: number) =>
  `rgba(${r | 0}, ${g | 0}, ${b | 0}, ${a.toFixed(3)})`

/**
 * 画出光谱：每座峰由一束细丝般的高斯曲线织成，峰体内是竖向谱线，峰顶有标记点。
 * 指针附近的曲线被「吸起」，谱线被点亮——这是背景唯一的交互反馈。
 */
export function drawSpectrum(ctx: CanvasRenderingContext2D, f: Frame) {
  const { width: W, height: H, dark, time, pointer } = f
  const base = f.baseline * H
  const amp = base * 0.9
  const step = Math.max(4, Math.round(W / 280))
  const lift = (x: number) => pointer.s * 52 * gaussian(x, pointer.x, W * 0.045)

  ctx.clearRect(0, 0, W, H)
  ctx.globalCompositeOperation = dark ? 'lighter' : 'source-over'

  // 指针光晕：取离指针最近的峰色
  if (pointer.s > 0.01) {
    const near = f.peaks.reduce((a, b) =>
      b.h > 0.01 && Math.abs(b.x * W - pointer.x) < Math.abs(a.x * W - pointer.x) ? b : a,
    )
    const glow = ctx.createRadialGradient(pointer.x, pointer.y, 0, pointer.x, pointer.y, 220)
    glow.addColorStop(0, rgba(near.rgb, (dark ? 0.2 : 0.14) * pointer.s))
    glow.addColorStop(1, rgba(near.rgb, 0))
    ctx.fillStyle = glow
    ctx.fillRect(pointer.x - 220, pointer.y - 220, 440, 440)
  }

  f.peaks.forEach((p, pi) => {
    if (p.h < 0.01) return
    const cx = p.x * W
    const pw = p.w * W
    const ph = p.h * amp
    const left = cx - pw * 6
    const right = cx + pw * 6
    const grad = ctx.createLinearGradient(left, 0, right, 0)
    grad.addColorStop(0, rgba(p.rgb, 0))
    grad.addColorStop(0.5, rgba(p.rgb, dark ? 0.55 : 0.46))
    grad.addColorStop(1, rgba(p.rgb, 0))
    ctx.strokeStyle = grad
    ctx.lineWidth = dark ? 0.8 : 0.7

    // 细丝：宽度逐层展开、高度逐层降低，并随时间轻微呼吸与摆动
    for (let s = 0; s < STRANDS; s++) {
      const k = s / (STRANDS - 1)
      const breathe = 1 + 0.07 * Math.sin(time * 0.0007 + s * 0.45 + pi * 1.3)
      const sw = pw * (0.7 + 0.9 * k)
      const sh = ph * (0.42 + 0.58 * (1 - k)) * breathe
      const sway = pw * 0.22 * Math.sin(time * 0.0004 + s * 0.3 + pi)
      ctx.beginPath()
      for (let x = left; x <= right; x += step) {
        const g = gaussian(x, cx + sway, sw)
        const silk = 5 * Math.sin(x * 0.004 + time * 0.0005 + s * 0.5) * (1 - g)
        const y = base - sh * g - lift(x) * (0.35 + 0.65 * g) + silk
        if (x === left) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.stroke()
    }

    // 谱线：峰体内的竖线，指针经过处变亮
    ctx.lineWidth = 1
    for (let x = cx - pw * 2.2; x <= cx + pw * 2.2; x += 6) {
      const top = base - ph * gaussian(x, cx, pw) - lift(x)
      const near = pointer.s * gaussian(x, pointer.x, 60)
      ctx.strokeStyle = rgba(p.rgb, (dark ? 0.2 : 0.15) + near * 0.5)
      ctx.beginPath()
      ctx.moveTo(x, base)
      ctx.lineTo(x, top)
      ctx.stroke()
    }

    // 峰顶标记：一根细竖线 + 两个点（不标注数值）
    // 标记线最高只到画布上沿以内，避免被裁掉一截
    const peakTop = base - ph - lift(cx)
    const markTop = Math.max(8, peakTop - H * 0.1)
    ctx.strokeStyle = dark ? 'rgba(255,255,255,0.28)' : 'rgba(30,28,40,0.28)'
    ctx.beginPath()
    ctx.moveTo(cx, base)
    ctx.lineTo(cx, markTop)
    ctx.stroke()
    ctx.fillStyle = rgba(p.rgb, 0.95)
    for (const y of [peakTop, markTop]) {
      ctx.beginPath()
      ctx.arc(cx, y, 2.4, 0, Math.PI * 2)
      ctx.fill()
    }
  })
  ctx.globalCompositeOperation = 'source-over'
}
