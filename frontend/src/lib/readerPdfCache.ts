/** Bounded private PDF cache. Persistent keys are digests, never credentials. */
const entries = new Map<string, { bytes: ArrayBuffer; expires: number }>()
const MAX_BYTES = 48 * 1024 * 1024
const TTL_MS = 30 * 60 * 1000
const CACHE_NAME = 'dr-reader-pdf-v1'
const DISK_MAX_BYTES = 96 * 1024 * 1024
const DISK_TTL_MS = 24 * 60 * 60 * 1000
let size = 0
let generation = 0
let pending: Promise<unknown> = Promise.resolve()

export function clearReaderPdfMemory() {
  entries.clear()
  size = 0
}

export function readerPdfCacheGeneration() {
  return generation
}

// Serialize writes/deletes, so logout cannot race an old download and leave
// private bytes behind. Unavailable/full browser storage is a cache miss.
function onDisk<T>(operation: () => Promise<T>): Promise<T | null> {
  const next = pending
    .then(async () => {
      if (typeof caches === 'undefined' || !globalThis.crypto?.subtle) return null
      return operation()
    })
    .catch(() => null)
  pending = next
  return next
}

export function clearReaderPdfCache(): Promise<unknown> {
  generation += 1
  clearReaderPdfMemory()
  return onDisk(() => caches.delete(CACHE_NAME))
}

async function diskKey(key: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key))
  const hex = Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, '0'),
  ).join('')
  return `${location.origin}/__reader_pdf_cache__/${hex}`
}

export function persistentReaderPdf(key: string, epoch: number): Promise<ArrayBuffer | null> {
  return onDisk(async () => {
    if (epoch !== generation) return null
    const cache = await caches.open(CACHE_NAME)
    const url = await diskKey(key)
    const response = await cache.match(url)
    if (!response) return null
    const expires = Number(response.headers.get('X-Pdf-Expires'))
    if (!Number.isFinite(expires) || expires <= Date.now()) {
      await cache.delete(url)
      return null
    }
    const bytes = await response.arrayBuffer()
    if (epoch !== generation) return null
    cacheReaderPdf(key, bytes, Math.min(expires, Date.now() + TTL_MS))
    return bytes
  })
}

export function persistReaderPdf(key: string, bytes: ArrayBuffer, epoch: number): Promise<unknown> {
  if (epoch !== generation || bytes.byteLength > DISK_MAX_BYTES) return Promise.resolve()
  // The caller/PDF.js may transfer bytes while the disk operation is queued.
  const copy = bytes.slice(0)
  return onDisk(async () => {
    if (epoch !== generation) return
    const cache = await caches.open(CACHE_NAME)
    const url = await diskKey(key)
    const kept: { request: Request; size: number }[] = []
    let total = 0
    for (const request of await cache.keys()) {
      const response = await cache.match(request)
      const expires = Number(response?.headers.get('X-Pdf-Expires'))
      const length = Number(response?.headers.get('Content-Length'))
      if (
        request.url === url ||
        !Number.isFinite(expires) ||
        expires <= Date.now() ||
        !(length > 0)
      ) {
        await cache.delete(request)
      } else {
        kept.push({ request, size: length })
        total += length
      }
    }
    for (const entry of kept) {
      if (total + copy.byteLength <= DISK_MAX_BYTES) break
      await cache.delete(entry.request)
      total -= entry.size
    }
    if (epoch !== generation) return
    await cache.put(
      url,
      new Response(copy, {
        headers: {
          'Content-Type': 'application/pdf',
          'Content-Length': String(copy.byteLength),
          'X-Pdf-Expires': String(Date.now() + DISK_TTL_MS),
        },
      }),
    )
  })
}

export function cachedReaderPdf(key: string): ArrayBuffer | null {
  const entry = entries.get(key)
  if (!entry) return null
  if (entry.expires <= Date.now()) {
    entries.delete(key)
    size -= entry.bytes.byteLength
    return null
  }
  entries.delete(key)
  entries.set(key, entry)
  // PDF.js transfers buffers to its worker. Never give it the cached buffer.
  return entry.bytes.slice(0)
}

export function cacheReaderPdf(key: string, bytes: ArrayBuffer, expires = Date.now() + TTL_MS) {
  if (bytes.byteLength > MAX_BYTES) return
  const previous = entries.get(key)
  if (previous) size -= previous.bytes.byteLength
  entries.delete(key)
  while (size + bytes.byteLength > MAX_BYTES && entries.size) {
    const first = entries.entries().next().value!
    entries.delete(first[0])
    size -= first[1].bytes.byteLength
  }
  entries.set(key, { bytes: bytes.slice(0), expires })
  size += bytes.byteLength
}
