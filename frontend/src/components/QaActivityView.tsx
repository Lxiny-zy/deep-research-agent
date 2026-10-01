import type { QaActivity } from '../types'
import { qaActivityEvents } from '../lib/qaActivity'
import ModelReasoningPanel from './ModelReasoningPanel'
import ModelUsagePanel from './ModelUsagePanel'

export default function QaActivityView({
  items,
  live = false,
}: {
  items: QaActivity[]
  live?: boolean
}) {
  const events = qaActivityEvents(items)
  const cache = items.find((item) => item.type === 'cache')
  return (
    <>
      {cache?.message && <p className="hint">{cache.message}</p>}
      <ModelReasoningPanel events={events} live={live} />
      <ModelUsagePanel events={events} />
    </>
  )
}
