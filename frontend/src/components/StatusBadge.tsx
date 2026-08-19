import { AppIcon, type AppIconName } from './AppIcon'
import type { RunStatus } from '../types'

const LABEL: Record<RunStatus, string> = {
  pending: '排队中',
  running: '进行中',
  cancelling: '取消中',
  cancelled: '已取消',
  done: '已完成',
  error: '出错',
}

const BADGE_CLASS: Record<RunStatus, string> = {
  pending: 'badge warning',
  running: 'badge info',
  cancelling: 'badge warning',
  cancelled: 'badge muted',
  done: 'badge success',
  error: 'badge error',
}

const ICON: Record<RunStatus, AppIconName> = {
  pending: 'clock',
  running: 'loader',
  cancelling: 'loader',
  cancelled: 'circle-x',
  done: 'check-circle',
  error: 'circle-x',
}

export default function StatusBadge({ status }: { status: RunStatus }) {
  return (
    <span className={BADGE_CLASS[status]}>
      <AppIcon name={ICON[status]} size={13} aria-hidden="true" />
      {LABEL[status]}
    </span>
  )
}
