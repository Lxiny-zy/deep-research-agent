// 角色行为模板的中文标签与说明（与后端 BEHAVIORS 对齐）。
import type { Behavior } from '../types'

export const BEHAVIOR_LABELS: Record<Behavior, string> = {
  plan: '规划',
  research: '检索',
  reflect: '反思',
  synthesize: '综合',
  critique: '评审',
}

export const BEHAVIOR_HINTS: Record<Behavior, string> = {
  plan: '把研究问题拆解为可独立检索的子问题',
  research: '针对子问题检索网络并抽取带出处的发现',
  reflect: '评估证据是否充分，提出补洞子问题',
  synthesize: '把发现综合成带引用的研究报告',
  critique: '对报告做批判性复核，给出改进意见',
}

export function behaviorLabel(b: string): string {
  return BEHAVIOR_LABELS[b as Behavior] ?? b
}
