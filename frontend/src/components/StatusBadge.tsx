import type { RunStatus } from '../types'

const LABEL: Record<RunStatus, string> = {
  pending: '排队中',
  running: '进行中',
  done: '已完成',
  error: '出错',
}

export default function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`badge badge-${status}`}>{LABEL[status]}</span>
}
