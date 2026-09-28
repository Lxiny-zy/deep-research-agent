import '@testing-library/jest-dom/vitest'

// jsdom 没有 canvas 实现，调用 getContext 会打印 "Not implemented"。
// 背景动效组件在拿不到 2D 上下文时本就退化为纯 CSS，这里让测试走同一条退化路径。
HTMLCanvasElement.prototype.getContext = (() =>
  null) as typeof HTMLCanvasElement.prototype.getContext
