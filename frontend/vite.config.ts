/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 前端由后端同源托管在 '/'；开发期用 proxy 把 API/SSE 转发到 FastAPI(127.0.0.1:8000)，
// 因此前端 5173 与后端同源调用，无需 CORS。生产构建产物输出到 frontend/dist，
// 后端 api.py 会优先加载 dist/index.html。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/healthz': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
  },
})
