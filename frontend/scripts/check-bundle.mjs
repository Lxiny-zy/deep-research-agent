import { readFile, readdir, stat } from 'node:fs/promises'
import { gzipSync } from 'node:zlib'

const dist = new URL('../dist/', import.meta.url)
const html = await readFile(new URL('index.html', dist), 'utf8')
const initial = [...new Set([...html.matchAll(/(?:src|href)="(\/assets\/[^" ]+\.js)"/g)].map(m => m[1]))]
if (!initial.length) throw new Error('No initial JavaScript assets found')
let initialBytes = 0
let initialGzipBytes = 0
for (const asset of initial) {
  if (/markdown|workflow-canvas|ResearchField/i.test(asset)) {
    throw new Error(`A deferred feature was added to the initial load: ${asset}`)
  }
  const content = await readFile(new URL(asset.slice(1), dist))
  initialBytes += content.length
  initialGzipBytes += gzipSync(content).length
}
let cssGzipBytes = 0
for (const asset of await readdir(new URL('assets/', dist))) {
  const target = new URL(`assets/${asset}`, dist)
  if (asset.endsWith('.css')) cssGzipBytes += gzipSync(await readFile(target)).length
  if (asset.endsWith('.js') && (await stat(target)).size > 650 * 1024) {
    throw new Error(`JavaScript chunk exceeds 650 KiB: ${asset}`)
  }
}
const report = { initial, initialBytes, initialGzipBytes, cssGzipBytes }
console.log(JSON.stringify(report, null, 2))
if (initialBytes > 600 * 1024 || initialGzipBytes > 180 * 1024 || cssGzipBytes > 85 * 1024) {
  throw new Error('Bundle budget exceeded (initial JS 600 KiB / gzip 180 KiB; CSS gzip 85 KiB)')
}
