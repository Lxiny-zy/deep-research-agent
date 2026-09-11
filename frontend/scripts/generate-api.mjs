import { execFileSync } from 'node:child_process'
import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'

const root = fileURLToPath(new URL('../../', import.meta.url))
const target = new URL('../src/api/schema.d.ts', import.meta.url)
const schema = JSON.parse(
  execFileSync(process.env.PYTHON ?? 'python', ['scripts/export_openapi.py'], {
    cwd: root,
    encoding: 'utf8',
    maxBuffer: 10 * 1024 * 1024,
  }),
)
const source =
  '// Generated from FastAPI OpenAPI. Run npm run api:generate; do not edit.\n' +
  astToString(await openapiTS(schema, { defaultNonNullable: false }))
if (process.argv.includes('--check')) {
  if ((await readFile(target, 'utf8')).replaceAll('\r\n', '\n') !== source) {
    throw new Error('API types have changed. Run npm run api:generate and review the diff.')
  }
  console.log('OpenAPI types are current')
} else {
  await writeFile(target, source)
  console.log('Generated src/api/schema.d.ts')
}
