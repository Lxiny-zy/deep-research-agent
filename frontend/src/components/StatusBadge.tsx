import type { RunStatus } from '../types'

const LABEL: Record<RunStatus, string> = {
  pending: '排队中',
  running: '进行中',
  done: '已完成',
  error: '出错',
}

const BADGE_CLASS: Record<RunStatus, string> = {
  pending: 'badge warning',
  running: 'badge info',
  done: 'badge success',
  error: 'badge error',
}

const ICON: Record<RunStatus, React.ReactNode> = {
  pending: (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.5" fill="none"/>
      <path d="M8 5V8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  running: (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M5 3L13 8L5 13V3Z" fill="currentColor"/>
    </svg>
  ),
  done: (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M3 8L7 12L13 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  error: (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M4 4L12 12M12 4L4 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
    </svg>
  ),
}

export default function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <span className={BADGE_CLASS[status]} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
      {ICON[status]}
      {LABEL[status]}
    </span>
  )
}
