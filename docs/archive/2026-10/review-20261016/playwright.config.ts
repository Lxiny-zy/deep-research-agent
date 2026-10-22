import { defineConfig } from '../../frontend/node_modules/@playwright/test/index.mjs'

export default defineConfig({
  testDir: '.',
  testMatch: 'browser-probe.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 30000,
  outputDir: './browser-results',
  reporter: [['list'], ['json', { outputFile: './docs/review-20261016/browser-results.json' }]],
  use: { baseURL: 'http://127.0.0.1:5199', headless: true, viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5199 --strictPort',
    cwd: '../../frontend',
    url: 'http://127.0.0.1:5199',
    reuseExistingServer: false,
  },
})
