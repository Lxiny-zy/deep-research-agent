/**
 * 欢迎界面的「环外光谱」：把背景里的高斯光谱绕成一圈，
 * 从光环外沿凸起一簇簇细丝峰，随节拍律动、朝指针方向隆起，并向外飘散光尘。
 */

export interface HaloFrame {
  /** 画布边长（CSS 像素，正方形） */
  size: number
  /** 光环细线所在半径 */
  radius: number
  time: number
  /** 悬停「进入」时的能量 0..1 */
  energy: number
  /** 离场爆发 0..1 */
  burst: number
  /** 指针方向（弧度，0 = 右侧，顺时针）与影响强度 */
  pointer: { angle: number; s: number }
  dark: boolean
}

const TAU = Math.PI * 2
const STRANDS = 10
const STEPS = 360
const BEAT_MS = 2400
const DUST = 54

/** 环外的峰：角度（圈）、角宽（圈）、相对半径的高度 */
const PEAKS = [
  { a: 0.02, w: 0.022, h: 0.2 },
  { a: 0.16, w: 0.03, h: 0.13 },
  { a: 0.31, w: 0.018, h: 0.24 },
  { a: 0.47, w: 0.035, h: 0.11 },
  { a: 0.6, w: 0.02, h: 0.19 },
  { a: 0.74, w: 0.028, h: 0.15 },
  { a: 0.87, w: 0.016, h: 0.22 },
]

const LIGHT = ['#e8508a', '#f0913a', '#d9b21c', '#2fb58a', '#2aa7d6', '#4a64e0', '#9150d8']
const DARK = ['#ff9fc2', '#ffc56b', '#fff1a0', '#8fe3c8', '#7fe0ff', '#7aa7ff', '#c7a4ff']

/** 两个角度（圈）之间最短的环向距离 */
const turnDist = (a: number, b: number) => {
  const d = Math.abs(a - b) % 1
  return d > 0.5 ? 1 - d : d
}

const bell = (d: number, w: number) => Math.exp(-(d * d) / (2 * w * w))

/** 节拍：每 2.4 秒一次短促的「心跳」，外加一次较弱的回响 */
export function beat(time: number) {
  const phase = (time % BEAT_MS) / BEAT_MS
  const main = Math.exp(-((phase / 0.07) ** 2))
  const echo = 0.45 * Math.exp(-(((phase - 0.2) / 0.06) ** 2))
  return Math.min(1, main + echo)
}

export function drawHalo(ctx: CanvasRenderingContext2D, f: HaloFrame) {
  const { size, radius: R, time, dark, energy, burst, pointer } = f
  const c = size / 2
  const pulse = beat(time)
  const spin = time * 0.000012
  const gain = (1 + 0.38 * pulse + 0.7 * energy) * (1 + 2.2 * burst)
  const pAngle = pointer.angle / TAU

  // 每个角度上的总凸起高度（相对半径）
  const heightAt = (turn: number, widen: number) => {
    let h = 0
    PEAKS.forEach((p, i) => {
      const sway = 0.5 + 0.5 * Math.sin(time * 0.0011 + i * 1.7)
      h += p.h * (0.6 + 0.4 * sway) * bell(turnDist(turn, p.a + spin), p.w * widen)
    })
    h += 0.16 * pointer.s * bell(turnDist(turn, pAngle), 0.03 * widen)
    // 正下方（0.25 圈）留一段安静区，给光环下方的品牌字让位
    const quiet = 1 - 0.8 * bell(turnDist(turn, 0.25), 0.055)
    return h * gain * quiet
  }

  ctx.clearRect(0, 0, size, size)
  ctx.globalCompositeOperation = dark ? 'lighter' : 'source-over'
  const colors = dark ? DARK : LIGHT
  let paint: CanvasGradient | string = colors[5]
  if (typeof ctx.createConicGradient === 'function') {
    const g = ctx.createConicGradient(spin * TAU, c, c)
    colors.forEach((col, i) => g.addColorStop(i / colors.length, col))
    g.addColorStop(1, colors[0])
    paint = g
  }
  ctx.strokeStyle = paint
  ctx.fillStyle = paint

  // 细丝：一圈圈闭合曲线，外层更宽更矮
  for (let s = 0; s < STRANDS; s++) {
    const k = s / (STRANDS - 1)
    const widen = 0.7 + 1.1 * k
    const scale = 0.4 + 0.6 * (1 - k)
    ctx.globalAlpha = (dark ? 0.5 : 0.42) * (0.45 + 0.55 * (1 - k))
    ctx.lineWidth = dark ? 0.9 : 0.8
    ctx.beginPath()
    for (let i = 0; i <= STEPS; i++) {
      const turn = i / STEPS
      const silk = 0.006 * Math.sin(turn * TAU * 9 + time * 0.0009 + s)
      const r = R * (1.01 + silk * (1 - k) + heightAt(turn, widen) * scale)
      const x = c + r * Math.cos(turn * TAU)
      const y = c + r * Math.sin(turn * TAU)
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }

  // 谱线：峰体内的径向细线，节拍到来时整体一亮
  ctx.lineWidth = 1
  for (let i = 0; i < 300; i++) {
    const turn = i / 300
    const h = heightAt(turn, 1)
    if (h < 0.025) continue
    ctx.globalAlpha = Math.min(1, (dark ? 0.2 : 0.16) + h * 0.9 + pulse * 0.12)
    const cos = Math.cos(turn * TAU)
    const sin = Math.sin(turn * TAU)
    ctx.beginPath()
    ctx.moveTo(c + R * 1.01 * cos, c + R * 1.01 * sin)
    ctx.lineTo(c + R * (1.01 + h) * cos, c + R * (1.01 + h) * sin)
    ctx.stroke()
  }

  // 峰顶标记：一根外伸的细线 + 两个点（不标注数值）
  PEAKS.forEach((p) => {
    const turn = p.a + spin
    const tip = 1.01 + heightAt(turn, 1)
    const cos = Math.cos(turn * TAU)
    const sin = Math.sin(turn * TAU)
    ctx.globalAlpha = dark ? 0.35 : 0.3
    ctx.beginPath()
    ctx.moveTo(c + R * tip * cos, c + R * tip * sin)
    ctx.lineTo(c + R * (tip + 0.09) * cos, c + R * (tip + 0.09) * sin)
    ctx.stroke()
    ctx.globalAlpha = 0.95
    for (const t of [tip, tip + 0.09]) {
      ctx.beginPath()
      ctx.arc(c + R * t * cos, c + R * t * sin, 2.2, 0, TAU)
      ctx.fill()
    }
  })

  // 光尘：从环上向外飘散，越远越淡
  for (let i = 0; i < DUST; i++) {
    const turn = (i * 0.618034 + spin * 3) % 1
    const life = (time * (0.00011 + (i % 7) * 0.000013) + i * 0.37) % 1
    const r = R * (1.03 + life * (0.5 + 0.4 * energy + 1.5 * burst))
    ctx.globalAlpha = Math.sin(life * Math.PI) * (dark ? 0.75 : 0.55)
    ctx.beginPath()
    ctx.arc(c + r * Math.cos(turn * TAU), c + r * Math.sin(turn * TAU), 1.1 + (i % 3) * 0.4, 0, TAU)
    ctx.fill()
  }

  ctx.globalAlpha = 1
  ctx.globalCompositeOperation = 'source-over'
}
