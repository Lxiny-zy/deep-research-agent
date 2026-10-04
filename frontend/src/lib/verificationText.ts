const MESSAGES: Record<string, string> = {
  invalid_citation: '引用编号未对应到本次材料，需要修正。',
  uncited_paragraph: '部分事实没有注明依据，需要补充引用。',
  normalized_quote: '摘录已与原文匹配。',
  quote_found_in_source: '已在来源原文中找到对应摘录。',
  evidence_quote_not_found: '未能在当前来源中找到对应摘录。',
  evidence_quote_too_short: '摘录过短，尚不足以核对这条论断。',
  evidence_quote_too_long: '摘录超过长度上限，需要从原文重新选择能支持该论断的连续短引文。',
  source_url_mismatch: '摘录的来源地址与当前来源不一致。',
  source_retracted: '来源已标记为撤稿，不能用于支持正式结论。',
  semantic_indices_invalid: '核验返回的证据对应关系不完整，本项判断暂不可用。',
  semantic_evidence_exceeds_input_capacity: '完整证据超过当前模型的处理范围，语义核验尚未完成。',
  no_independent_corroboration: '目前没有其他独立来源支持这条论断。',
  independent_sources_corroborate_claim: '其他独立来源也支持这条论断。',
  contradiction_detected: '来源之间存在分歧，需要结合原文进一步核对。',
  quantity_found_in_evidence: '数值、单位及其对应关系已在原文中核对。',
  no_measurement_in_evidence: '原文摘录中没有找到与该数值对应的测量结果。',
  quantity_rendered_value_mismatch: '显示的数值与记录值不一致，需要重新核对。',
  quantity_rendered_ambiguous_scientific_notation: '原文的科学计数法存在歧义，需要进一步核对。',
  quantity_value_not_finite: '数值记录无效，不能据此形成结论。',
  comparator_found_in_evidence: '数值的比较关系已在原文中核对。',
  comparator_not_declared: '没有提供需要核对的数值比较关系。',
  comparator_not_in_evidence: '原文未支持这条数值比较关系。',
}

/** Human explanations only; machine diagnostics stay unchanged in the source records. */
export function verificationText(
  reason: string | undefined,
  fallback = '暂无进一步的核验说明。',
): string {
  const text = reason?.trim()
  if (!text) return fallback
  const translated = text.replace(
    /\b(semantic_verifier_failed|consistency_verifier_failed)(?::([A-Za-z][A-Za-z0-9_]*))?\b/g,
    (_match, stage: string, error: string | undefined) => {
      const label =
        stage === 'semantic_verifier_failed' ? '论断与原文的支持关系核对' : '来源之间的一致性核对'
      const detail = error?.includes('Timeout')
        ? '服务响应超时，请稍后重试。'
        : error === 'ValueError' || error === 'ValidationError'
          ? '返回结果未能通过检查，请稍后重试。'
          : '服务暂时未能完成检查，请稍后重试。'
      return `${label}未完成。${detail}`
    },
  )
  if (translated !== text) return translated
  if (MESSAGES[text]) return MESSAGES[text]
  const readable = text.replace(
    /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g,
    (code) => MESSAGES[code] ?? code,
  )
  if (readable !== text) return readable
  if (text.startsWith('quantity_section_not_allowed:'))
    return '该数值来自当前核验范围之外的章节，尚不能据此确认实验结果。'
  if (/^(?:核验调用失败[:：]\s*)?[A-Za-z]+(?:Error|Exception)$/.test(text))
    return '核验过程未能完成，相关结果暂不能确认，请稍后重试。'
  if (/^[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?:[:：]\S+)?$/.test(text))
    return /_(?:failed|error|unavailable)(?::|$)/.test(text)
      ? '此项核验未完成，请稍后重试。'
      : fallback
  return text
}
