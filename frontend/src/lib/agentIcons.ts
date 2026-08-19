import type { AppIconName } from '../components/AppIcon'
import type { Behavior } from '../types'

const AGENT_ICON_BY_BEHAVIOR: Record<Behavior, AppIconName> = {
  plan: 'route',
  research: 'search-code',
  reflect: 'refresh',
  synthesize: 'file',
  critique: 'shield',
}

const AGENT_ICON_ALIASES: Record<string, AppIconName> = {
  bot: 'bot',
  brain: 'brain',
  route: 'route',
  plan: 'route',
  research: 'search-code',
  search: 'search-code',
  'search-code': 'search-code',
  reflect: 'refresh',
  refresh: 'refresh',
  synthesize: 'file',
  file: 'file',
  critique: 'shield',
  shield: 'shield',
  network: 'network',
  workflow: 'workflow',
}

export const AGENT_ICON_OPTIONS: { value: string; label: string; icon: AppIconName }[] = [
  { value: 'bot', label: '通用角色', icon: 'bot' },
  { value: 'route', label: '规划编排', icon: 'route' },
  { value: 'search', label: '研究检索', icon: 'search-code' },
  { value: 'reflect', label: '反思校验', icon: 'refresh' },
  { value: 'synthesize', label: '报告综合', icon: 'file' },
  { value: 'critique', label: '质量评审', icon: 'shield' },
  { value: 'network', label: '协作网络', icon: 'network' },
]

export function agentIconName(value?: string | null, behavior?: Behavior): AppIconName {
  const normalized = value?.trim().toLowerCase()
  if (normalized && AGENT_ICON_ALIASES[normalized]) return AGENT_ICON_ALIASES[normalized]
  return behavior ? AGENT_ICON_BY_BEHAVIOR[behavior] : 'bot'
}
