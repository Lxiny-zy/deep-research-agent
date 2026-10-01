/** Bounded, identity-scoped PDF bytes. Never persist credentials or PDF data to storage. */
const entries = new Map<string, { bytes: ArrayBuffer; expires: number }>()
const MAX_BYTES = 48 * 1024 * 1024
const TTL_MS = 30 * 60 * 1000
let size = 0

export function clearReaderPdfCache() {
  entries.clear()
  size = 0
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

export function cacheReaderPdf(key: string, bytes: ArrayBuffer) {
  if (bytes.byteLength > MAX_BYTES) return
  const previous = entries.get(key)
  if (previous) size -= previous.bytes.byteLength
  entries.delete(key)
  while (size + bytes.byteLength > MAX_BYTES && entries.size) {
    const first = entries.entries().next().value!
    entries.delete(first[0])
    size -= first[1].bytes.byteLength
  }
  entries.set(key, { bytes: bytes.slice(0), expires: Date.now() + TTL_MS })
  size += bytes.byteLength
}
