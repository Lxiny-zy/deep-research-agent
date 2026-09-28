/**
 * 设置页各字段的悬浮说明。交付质量字段的说明由后端 /api/config/quality-schema 提供
 * （与验收逻辑同源）；这里只放研究行为与模型相关的既有字段。
 */
export const SETTINGS_HELP: Record<string, string> = {
  llm_model:
    '没有为角色单独绑定模型档案时使用的模型 ID（例如 gpt-4o-mini、deepseek-chat）。所有模型都通过 API 调用云端服务。',
  llm_base_url:
    'OpenAI 兼容接口的地址。留空使用官方端点；使用 DeepSeek、通义、智谱等兼容服务时填写对应的 /v1 地址。',
  llm_api_key:
    '全局兜底的模型密钥，只保存在服务端，界面不会回显。角色绑定了模型档案时优先使用档案自己的密钥。',
  search_profiles:
    '未单独绑定检索服务的角色使用这些检索档案。勾选多个时并发检索并合并去重来源；学术类任务会自动补上 OpenAlex 与 arXiv。',
  max_sub_questions:
    '一次研究最多把问题拆成多少个子问题并行检索。越多覆盖越广，但耗时与 token 成比例增加。任务档位会在此基础上再收紧。',
  max_rounds:
    '反思补洞的最大轮数：每轮由质检员判断证据是否足够，不足则追加子问题继续检索。0 表示不补洞。',
  max_concurrency:
    '同时进行的检索与抽取数量上限。调高能缩短耗时，但更容易触发检索服务或模型服务的限流。',
  results_per_search:
    '每个子问题取回多少条检索结果参与证据抽取。调高增加候选来源，也会增加阅读与核验的 token 消耗。',
  fulltext_max_chars:
    '解析 arXiv LaTeX 全文时，每篇论文最多保留多少字符参与证据抽取。按章节筛选后截断，越大越完整、越耗 token。',
  max_run_seconds:
    '单次运行的最长时间（秒）。到时停止后续检索，用已核验的证据完成写作并如实标注未完成的部分。',
  fulltext_enabled:
    '开启后，arXiv 来源优先下载 e-print 并按章节抽取正文作为证据；下载或解析失败时自动回退到摘要。',
  request_timeout: '单次调用模型或检索服务的超时时间（秒）。网络较慢或使用推理模型时可适当调高。',
  require_corroboration:
    '开启后，只有至少两个独立发布方交叉印证、且没有冲突的论断才能进入报告。更可靠，但可用证据会明显减少。',
}
