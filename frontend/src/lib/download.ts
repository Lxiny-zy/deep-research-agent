// 浏览器端文件导出 / 文件名工具（无第三方依赖）。

// 触发浏览器下载一段文本为文件。
export function downloadText(filename: string, text: string, mime = 'text/markdown'): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` })
  downloadBlob(filename, blob)
}

export function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // 释放 objectURL：放到下一帧，避免个别浏览器在 click 异步处理前就回收
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

// 把研究问题压成安全的文件名片段：去标点、空白转连字符、限长。
export function slugify(text: string, maxLen = 40): string {
  const slug = (text || '')
    .trim()
    .replace(/[\s/\\]+/g, '-') // 空白与路径分隔符 → 连字符
    .replace(/[^\p{L}\p{N}-]/gu, '') // 仅保留字母/数字/连字符（含中文）
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, maxLen)
  return slug || 'report'
}
