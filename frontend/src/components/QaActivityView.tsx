import type { QaActivity } from '../types'
import { qaActivityEvents } from '../lib/qaActivity'
import ModelReasoningPanel from './ModelReasoningPanel'

export default function QaActivityView({
  items,
  live = false,
}: {
  items: QaActivity[]
  live?: boolean
}) {
  const events = qaActivityEvents(items)
  return <ModelReasoningPanel events={events} live={live} />
}
