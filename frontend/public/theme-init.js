// 首帧前应用主题，避免深色用户看到一闪而过的浅色页面。
// 独立文件而非内联脚本：生产环境的 CSP 为 script-src 'self'。
try {
  var theme = localStorage.getItem('sr_theme')
  if (theme !== 'light' && theme !== 'dark') {
    theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  document.documentElement.dataset.theme = theme
} catch (error) {
  // 存储不可用时由应用启动后按系统主题设置
}
