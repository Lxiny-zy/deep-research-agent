import type { AppIconName } from '../components/AppIcon'
import type { DeliverableFormat } from '../types'

const FORMAT_LABEL: Record<DeliverableFormat, string> = {
  md: 'Markdown',
  docx: 'Word',
  pdf: 'PDF',
  html: 'HTML',
  pptx: 'PPT',
  mindmap: '导图',
  xlsx: 'Excel',
  zip: 'ZIP',
}

const TEMPLATE_ICONS = new Set<AppIconName>([
  'network',
  'library',
  'shield',
  'book',
  'chart',
  'presentation',
  'mindmap',
])

/** 运行步骤里的角色名 → 用户可读的步骤名。未知角色（自定义卡片）原样显示。 */
const ROLE_LABEL: Record<string, string> = {
  planner: '拆解研究问题',
  researcher: '检索与核验证据',
  reflector: '评估证据充分性',
  synthesizer: '撰写研究报告',
  critic: '批判性复核',
  aggregator: '归并团队结果',
  coordinator: '组织研究流程',
  intent_router: '意图门禁',
  paper_intake: '取回指定论文',
  attachment_reader: '阅读上传文件',
  research_writer: '撰写调研报告',
  survey_writer: '撰写文献综述',
  peer_reviewer: '撰写评审意见',
  paper_reader: '撰写精读报告',
  slide_writer: '生成演示文稿',
  mindmap_writer: '整理思维导图',
  data_analyst: '统计分析与报告',
  plan_executor: '执行计划步骤',
  operation_runner: '运行登记操作',
}

export function stepLabel(label: string, agent: string): string {
  return ROLE_LABEL[label] ?? ROLE_LABEL[agent] ?? label
}

/** 任务类型的英文副标题（衬线小字）；未知模板不显示。 */
const TEMPLATE_EN: Record<string, string> = {
  autoResearch: 'Research',
  litReview: 'Literature',
  peerReview: 'Peer Review',
  paperRead: 'Close Reading',
  dataAnalysis: 'Data',
  slides: 'Slides',
  mindmap: 'Mind Map',
}

export function templateEn(key: string): string {
  return TEMPLATE_EN[key] ?? ''
}

/** 模板图标名来自后端数据；未知名回退为通用图标，而不是渲染出一个空洞。 */
export function templateIcon(icon: string): AppIconName {
  return TEMPLATE_ICONS.has(icon as AppIconName) ? (icon as AppIconName) : 'sparkles'
}

export function formatLabel(format: string): string {
  return FORMAT_LABEL[format as DeliverableFormat] ?? format.toUpperCase()
}

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}
