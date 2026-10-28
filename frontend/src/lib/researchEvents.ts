import type { ResearchEvent } from '../types'
import { isTerminalRunStatus } from './runStatus'
import { stepLabel } from './workbench'

/** Streaming payloads belong to the report/reasoning views, not the activity log. */
export function isActivityEvent(event: ResearchEvent): boolean {
  // Failures and terminal events must remain visible even if they carry model data.
  if (isTerminalRunStatus(event.type)) return true
  return (
    event.type !== 'token' &&
    event.type !== 'report' &&
    typeof event.data?.reasoning_delta !== 'string' &&
    event.data?.llm_usage == null &&
    event.data?.model_call == null &&
    event.data?.model_queue == null
  )
}

/** Keep one reasoning record per call so fragments cannot evict real progress. */
export function appendResearchEvent(events: ResearchEvent[], next: ResearchEvent): ResearchEvent[] {
  const data = next.data
  if (
    next.type === 'info' &&
    typeof data?.reasoning_delta === 'string' &&
    typeof data.call_id === 'string'
  ) {
    const index = events.findIndex(
      (event) =>
        event.type === 'info' &&
        event.stage === next.stage &&
        event.data?.call_id === data.call_id &&
        typeof event.data?.reasoning_delta === 'string',
    )
    if (index >= 0) {
      return events.map((event, position) =>
        position === index
          ? {
              ...event,
              data: {
                ...event.data,
                ...data,
                reasoning_delta: String(event.data?.reasoning_delta) + data.reasoning_delta,
              },
            }
          : event,
      )
    }
  }
  return [...events.slice(-4999), next]
}

/** 编排层生命周期事件的中文说明；{step} 处填入步骤的中文名。 */
const RUNTIME_TEXT: Record<string, string> = {
  'workflow.started': '研究流程启动',
  'workflow.resumed': '从检查点恢复研究流程',
  'workflow.routed': '已选定研究流程',
  'execution.plan': '执行计划已就绪',
  'checkpoint.saved': '已保存检查点',
  'run.degraded': '部分步骤失败，按已有结果继续',
  'edge.condition_error': '分支条件计算出错，按默认路径继续',
  'step.ready': '准备：{step}',
  'step.started': '开始：{step}',
  'step.completed': '完成：{step}',
  'step.failed': '失败：{step}',
  'step.retrying': '重试：{step}',
  'step.skipped': '跳过：{step}',
  'step.cancelled': '取消：{step}',
}

const MACHINE_TEXT = /^[a-z_]+(\.[a-z_]+)+$/

/**
 * 活动流里展示的那一行字。编排层事件的 message 是机器名（如 step.started），
 * 这里换成中文；带步骤信息的补上步骤名。其余事件原样展示后端写好的中文说明。
 */
export function activityText(event: ResearchEvent): string {
  const data = event.data
  const name = typeof data?.event_name === 'string' ? data.event_name : ''
  const template = RUNTIME_TEXT[name] ?? (MACHINE_TEXT.test(event.message) ? RUNTIME_TEXT[event.message] : undefined)
  if (template) {
    if (!template.includes('{step}')) return template
    const label = typeof data?.label === 'string' ? data.label : ''
    const agent = typeof data?.agent === 'string' ? data.agent : ''
    return template.replace('{step}', stepLabel(label || agent, agent) || '步骤')
  }
  // 后端已写好人话的事件（如「研究流程已就绪，共 5 个阶段」）保持原样
  if (name && !MACHINE_TEXT.test(event.message)) return event.message
  return MACHINE_TEXT.test(event.message) ? '编排事件' : event.message
}
