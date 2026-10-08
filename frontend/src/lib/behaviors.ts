// 角色行为模板的中文标签与说明（与后端 BEHAVIORS 对齐）。
import type { Behavior } from '../types'

export const BEHAVIOR_LABELS: Record<Behavior, string> = {
  plan: '规划',
  research: '检索',
  reflect: '反思',
  synthesize: '综合',
  critique: '评审',
  research_writer: '课题调研写作', survey_writer: '文献综述写作', paper_reader: '论文精读',
  peer_reviewer: '论文审稿', slide_writer: '汇报幻灯片', mindmap_writer: '研究思维导图',
}

export const BEHAVIOR_HINTS: Record<Behavior, string> = {
  plan: '把研究问题拆解为可独立检索的子问题',
  research: '针对子问题检索网络并抽取带出处的发现',
  reflect: '评估证据是否充分，提出补洞子问题',
  synthesize: '把发现综合成带引用的研究报告',
  critique: '对报告做批判性复核，给出改进意见',
  research_writer: '保留课题调研的模板、引用核验和交付流程',
  survey_writer: '保留文献综述的比较结构、证据核验和交付流程',
  paper_reader: '保留论文精读、完整摘要翻译和依据核验流程',
  peer_reviewer: '保留论文审稿、评分和证据核验流程',
  slide_writer: '保留幻灯片结构、依据核验和生成流程',
  mindmap_writer: '保留导图结构、关系核验和生成流程',
}

export const BUILTIN_BEHAVIORS: Record<string, Behavior> = {
  planner: 'plan', researcher: 'research', reflector: 'reflect', synthesizer: 'synthesize', critic: 'critique',
  research_writer: 'research_writer', survey_writer: 'survey_writer', paper_reader: 'paper_reader',
  peer_reviewer: 'peer_reviewer', slide_writer: 'slide_writer', mindmap_writer: 'mindmap_writer',
}

export function behaviorLabel(b: string): string {
  return BEHAVIOR_LABELS[b as Behavior] ?? b
}
