import type { QaActivity, QaThought, ResearchEvent } from '../types'
import { modelCallRecord } from './modelCalls'

export function appendQaActivity(items: QaActivity[], next: QaActivity): QaActivity[] {
  if (next.type === 'model_call') {
    const call = modelCallRecord(next.model_call)
    if (!call) return items
    const index = items.findIndex((item) => item.type === 'model_call' && item.model_call?.call_id === call.call_id)
    if (index >= 0) {
      const previous = modelCallRecord(items[index].model_call)
      if (previous?.status !== 'started' && call.status === 'started') return items
      return items.map((item, position) => position === index ? next : item)
    }
  }
  if (next.type === 'reasoning' && next.call_id) {
    const index = items.findIndex(
      (item) => item.type === 'reasoning' && item.call_id === next.call_id,
    )
    if (index >= 0)
      return items.map((item, position) =>
        position === index
          ? {
              ...item,
              reasoning_delta: (item.reasoning_delta ?? '') + (next.reasoning_delta ?? ''),
            }
          : item,
      )
  }
  if (next.type === 'status' || next.type === 'cache' || next.type === 'reset') {
    return [...items.filter((item) => item.type !== next.type), next]
  }
  return [...items, next]
}

export function savedQaActivity(thoughts: QaThought[]): QaActivity[] {
  return thoughts.flatMap((thought): QaActivity[] => {
    if (thought.tool === 'model_reasoning')
      return [
        {
          type: 'reasoning',
          call_id: thought.call_id,
          model: thought.input,
          reasoning_delta: thought.observation,
        },
      ]
    if (thought.tool === 'model_usage') return [{ type: 'usage', llm_usage: thought.usage }]
    if (thought.tool === 'model_call') return [{ type: 'model_call', model_call: thought.call }]
    if (thought.tool === 'paper_cache') return [{ type: 'cache', message: thought.observation }]
    return []
  })
}

export function qaActivityEvents(items: QaActivity[]): ResearchEvent[] {
  return items.map((item) => ({
    stage: 'LLM',
    type: 'info',
    elapsed: 0,
    message: '',
    data:
      item.type === 'reasoning'
        ? {
            call_id: item.call_id,
            model: item.model,
            reasoning_delta: item.reasoning_delta,
          }
        : item.type === 'usage'
          ? { llm_usage: item.llm_usage }
          : item.type === 'model_call'
            ? { model_call: item.model_call }
            : {},
  }))
}
