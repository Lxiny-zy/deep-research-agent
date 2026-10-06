import { defineConfig } from '@playwright/test'

const managedApi = process.env.DR_BROWSER_MANAGED_API === '1'
const realApi = process.env.DR_BROWSER_REAL_API === '1' || managedApi
const baseURL = process.env.DR_BROWSER_BASE_URL || 'http://127.0.0.1:5199'
const target = new URL(baseURL)
const targeted = process.argv.some(
  (arg) =>
    ['--grep', '-g', '--grep-invert', '--last-failed'].includes(arg) ||
    arg.startsWith('--grep=') ||
    arg.endsWith('.spec.ts'),
)
const evidenceRoot = `../artifacts/browser-${realApi ? 'integration' : 'regression'}${targeted ? '/targeted' : ''}`
if (!['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname)) {
  throw new Error('Browser regression requires an isolated loopback environment.')
}
if (realApi && !process.env.DR_BROWSER_BASE_URL) {
  throw new Error('Real API checks require an explicitly supplied DR_BROWSER_BASE_URL.')
}

export default defineConfig({
  testDir: './tests/browser',
  testMatch: managedApi
    ? '**/integration-*.spec.ts'
    : realApi
      ? '**/real-api.spec.ts'
      : '**/controlled-*.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  metadata: {
    evidenceLayer: managedApi
      ? 'isolated-real-api-synthetic-records'
      : realApi
        ? 'isolated-real-api-read-only'
        : 'controlled-http-frontend',
    productionAcceptance: false,
    scope: targeted ? 'targeted' : 'full',
  },
  timeout: 45_000,
  expect: { timeout: 8_000 },
  outputDir: `${evidenceRoot}/results`,
  reporter: [
    ['list'],
    ['json', { outputFile: `${evidenceRoot}/results.json` }],
    [
      'html',
      {
        outputFolder: `${evidenceRoot}/report`,
        open: 'never',
      },
    ],
  ],
  use: {
    baseURL,
    browserName: 'chromium',
    headless: true,
    viewport: { width: 1440, height: 900 },
    contextOptions: { reducedMotion: 'reduce' },
    serviceWorkers: 'block',
    // Real identities must never be captured in traces or automatic screenshots.
    trace: realApi ? 'off' : 'retain-on-failure',
    screenshot: realApi ? 'off' : 'only-on-failure',
  },
  webServer: realApi
    ? undefined
    : {
        command: 'npm run dev -- --host 127.0.0.1 --port 5199 --strictPort',
        url: baseURL,
        reuseExistingServer: false,
        timeout: 30_000,
      },
})
