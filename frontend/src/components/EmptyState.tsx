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
    <div className="empty-state">
      <div className="empty-state-icon" aria-hidden="true">
        <AppIcon name={icon} size={22} strokeWidth={1.7} />
      </div>
      <h3 className="empty-state-title">{title}</h3>
      <p>{description}</p>
      {children && <div className="empty-state-actions">{children}</div>}
    </div>
  )
}
