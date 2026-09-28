import { AppIcon } from './AppIcon'

export default function RouteFallback() {
  return (
    <div className="spinner-container" role="status" aria-label="正在加载页面">
      <AppIcon name="loader" size={22} className="spin" aria-hidden="true" />
    </div>
  )
}
