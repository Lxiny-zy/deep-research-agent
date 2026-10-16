import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

/** Follow growth only while the reader is at the bottom; never scroll the page. */
export function useThreadScroll(scope: string, revision: string | number) {
  const [threadNode, setThreadNode] = useState<HTMLDivElement | null>(null)
  const [contentNode, setContentNode] = useState<HTMLDivElement | null>(null)
  const following = useRef(true)
  const [showLatest, setShowLatest] = useState(false)
  const jumpToLatest = useCallback(() => {
    const thread = threadNode
    if (!thread) return
    following.current = true
    thread.scrollTop = thread.scrollHeight
    setShowLatest(false)
  }, [threadNode])
  const onScroll = useCallback(() => {
    const thread = threadNode
    if (!thread) return
    following.current = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80
    setShowLatest(!following.current)
  }, [threadNode])
  useLayoutEffect(() => { jumpToLatest() }, [scope, jumpToLatest])
  useLayoutEffect(() => {
    if (following.current) jumpToLatest()
  }, [revision, jumpToLatest])
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return
    const thread = threadNode
    const content = contentNode
    if (!thread || !content) return
    let frame = 0
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        if (following.current) jumpToLatest()
      })
    })
    observer.observe(content)
    observer.observe(thread)
    return () => { observer.disconnect(); cancelAnimationFrame(frame) }
  }, [threadNode, contentNode, jumpToLatest])
  return { threadRef: setThreadNode, contentRef: setContentNode, onScroll, showLatest, jumpToLatest }
}
