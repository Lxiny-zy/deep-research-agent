import type { SearchProfile, SearchProvider } from '../types'

export const SEARCH_PROVIDERS: Record<SearchProvider, string> = {
  tavily: 'Tavily',
  brave: 'Brave',
  serper: 'Serper',
  grok: 'Grok Web Search',
  openalex: 'OpenAlex',
  arxiv: 'arXiv',
  responses: 'Responses 联网模型',
  chat_search: 'Chat Completions 搜索模型',
}

export const BUILTIN_SEARCH_PROFILES: SearchProfile[] = (
  ['tavily', 'brave', 'serper', 'grok', 'openalex', 'arxiv'] as SearchProvider[]
).map((provider) => ({
  id: `builtin:${provider}`,
  provider,
  name: SEARCH_PROVIDERS[provider],
  endpoint: '',
  model: '',
  key_ids: null,
  enabled: true,
  builtin: true,
}))

export function searchSelectionNames(ids: string[], profiles: SearchProfile[]) {
  return ids
    .map((id) => profiles.find((p) => p.id === id)?.name ?? `已失效的档案 (${id})`)
    .join(' / ')
}
