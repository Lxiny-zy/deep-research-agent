import type { ReactNode } from 'react'
import { AppIcon, type AppIconName } from './AppIcon'

export default function EmptyState({
  icon,
  title,
  description,
  children,
}: {
  icon: AppIconName
  title: string
  description: string
  children?: ReactNode
}) {
  return (
    <div className="empty-state workspace-empty">
      <div className="empty-state-icon">
        <AppIcon name={icon} size={26} strokeWidth={1.5} aria-hidden="true" />
      </div>
      <h3 className="empty-state-title">{title}</h3>
      <p>{description}</p>
      {children && <div className="empty-state-actions">{children}</div>}
    </div>
  )
}
