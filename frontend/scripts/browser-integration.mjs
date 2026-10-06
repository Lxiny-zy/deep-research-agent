import { spawn } from 'node:child_process'
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const root = path.dirname(frontend)
const port = 5208
const origin = `http://127.0.0.1:${port}`
const admin = 'synthetic-browser-admin-key'
const temporary = await mkdtemp(path.join(tmpdir(), 'dr-browser-integration-'))
const artifacts = path.join(root, 'artifacts/browser-integration')
await mkdir(artifacts, { recursive: true })
const npmCli = process.env.npm_execpath
if (!npmCli) throw new Error('Run this launcher through npm run test:browser:integration')
function command(program, args, options = {}) {
  const { logName, ...spawnOptions } = options
  return new Promise((resolve, reject) => {
    let output = ''
    const child = spawn(program, args, {
      cwd: frontend,
      windowsHide: true,
      stdio: logName ? ['ignore', 'pipe', 'pipe'] : 'inherit',
      ...spawnOptions,
    })
    if (logName) {
      child.stdout.on('data', (data) => {
        output += data.toString()
      })
      child.stderr.on('data', (data) => {
        output += data.toString()
      })
    }
    child.once('error', reject)
    child.once('exit', async (code) => {
      if (logName) await writeFile(path.join(artifacts, logName), output)
      code === 0
        ? resolve()
        : reject(new Error(`Command exited ${code}; inspect ${logName || 'test output'}`))
    })
  })
}
let server,
  serverOutput = ''
try {
  try {
    await fetch(`${origin}/healthz`, { signal: AbortSignal.timeout(500) })
    throw new Error('Port 5208 is already serving an application')
  } catch (error) {
    if (error.message.includes('already serving')) throw error
  }
  await command(process.execPath, [npmCli, 'run', 'build'], { logName: 'build.log' })
  const environment = Object.fromEntries(
    Object.entries(process.env).filter(([name]) =>
      /^(path|systemroot|windir|temp|tmp|userprofile|home|lang|lc_all)$/i.test(name),
    ),
  )
  Object.assign(environment, {
    PYTHONPATH: root,
    DR_BROWSER_FIXTURE_BOOTSTRAP: '1',
    PYTHONUTF8: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    DATABASE_URL: `sqlite+aiosqlite:///${path.join(temporary, 'browser.db').replaceAll('\\', '/')}`,
    DR_ARTIFACT_ROOT: path.join(temporary, 'files'),
    RUNTIME_CONFIG_PATH: path.join(temporary, 'runtime.json'),
    API_KEY: admin,
    DR_API_KEYS: JSON.stringify([
      { id: 'browser-alice', role: 'researcher', key: 'synthetic-browser-alice-key' },
      { id: 'browser-bob', role: 'researcher', key: 'synthetic-browser-bob-key' },
      { id: 'browser-reader', role: 'reader', key: 'synthetic-browser-reader-key' },
    ]),
    DR_EXECUTION_MODE: 'worker',
    DR_RENDER_MAX_PROCESSES: '1',
    INTENT_LLM_FALLBACK: '0',
  })
  server = spawn(
    process.env.DR_BROWSER_PYTHON || 'python',
    [path.join(frontend, 'tests/browser/server.py'), '--port', String(port)],
    { cwd: temporary, env: environment, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] },
  )
  server.stdout.on('data', (data) => {
    serverOutput += data.toString()
  })
  server.stderr.on('data', (data) => {
    serverOutput += data.toString()
  })
  const deadline = Date.now() + 45_000
  let ready = false
  while (Date.now() < deadline) {
    if (server.exitCode !== null)
      throw new Error(
        `Fixture API exited ${server.exitCode}; inspect artifacts/browser-integration/server.log`,
      )
    try {
      const response = await fetch(`${origin}/__browser_fixture__/state`, {
        headers: { Authorization: `Bearer ${admin}` },
        signal: AbortSignal.timeout(1000),
      })
      if (
        response.ok &&
        response.headers.get('content-type')?.includes('application/json') &&
        (await response.json()).run_id
      ) {
        ready = true
        break
      }
    } catch {
      /* startup has not bound the port yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  if (!ready) throw new Error('Fixture API did not become ready')
  await command(process.execPath, [npmCli, 'run', 'test:browser', '--', ...process.argv.slice(2)], {
    logName: 'tests.log',
    env: {
      ...process.env,
      DR_BROWSER_MANAGED_API: '1',
      DR_BROWSER_BASE_URL: origin,
    },
  })
  console.log('Real API browser suite passed; evidence: artifacts/browser-integration/')
} finally {
  if (server && server.exitCode === null) {
    await fetch(`${origin}/__browser_fixture__/shutdown`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${admin}` },
      signal: AbortSignal.timeout(2000),
    }).catch(() => {})
    await Promise.race([
      new Promise((resolve) => server.once('exit', resolve)),
      new Promise((resolve) => setTimeout(resolve, 10_000)),
    ])
    if (server.exitCode === null) server.kill()
  }
  await writeFile(path.join(artifacts, 'server.log'), serverOutput)
  // Only remove the exact temporary directory allocated by this invocation.
  if (
    path.dirname(path.resolve(temporary)) !== path.resolve(tmpdir()) ||
    !path.basename(temporary).startsWith('dr-browser-integration-')
  ) {
    throw new Error('Refusing to remove an unowned fixture path')
  }
  await rm(temporary, { recursive: true, force: true, maxRetries: 3 })
}
