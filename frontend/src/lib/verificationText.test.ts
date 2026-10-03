import { verificationText } from './verificationText'

it('explains failed checks without presenting them as source disagreement', () => {
  const message = verificationText('consistency_verifier_failed:ValueError')
  expect(message).toContain('一致性核对未完成')
  expect(message).toContain('返回结果未能通过检查')
  expect(message).not.toMatch(/ValueError|consistency_|来源之间存在分歧/)
  expect(verificationText('semantic_verifier_failed:TimeoutError')).toContain('响应超时')
})

it('translates machine reasons and preserves useful human explanations', () => {
  expect(verificationText('quote_found_in_source')).toContain('找到对应摘录')
  expect(verificationText('两个来源对实验条件的描述不同')).toBe('两个来源对实验条件的描述不同')
  expect(verificationText('The evidence supports correlation, not causation.')).toBe(
    'The evidence supports correlation, not causation.',
  )
  expect(verificationText('new_internal_code', '本项尚未检查')).toBe('本项尚未检查')
  expect(verificationText('new_verifier_failed:RuntimeError')).toContain('未完成')
  expect(verificationText('按核验问题修订回答：invalid_citation')).toBe(
    '按核验问题修订回答：引用编号未对应到本次材料，需要修正。',
  )
})
