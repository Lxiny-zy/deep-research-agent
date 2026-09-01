import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import PrintableReport from './PrintableReport'
import type { Finding } from '../types'

// 打印布局的性质，不是样式细节：
// - 侧栏是「按需」的（一次一条），纸上必须被**替换**成一次性呈现的附录；
// - 原先只活在 tooltip 里的信息（验证理由/置信度/完整哈希）必须落到纸上；
// - 缺失如实呈现：拿不到审计事件写「不可用」而不是 0；
// - 正文里 Synthesizer 追加的参考来源段落不能渲染两遍；
// - [n] 在纸上退回文本标记，不能保留按钮语义。

const URL_A = 'https://doi.org/10.1364/oe.1'
const URL_B = 'https://arxiv.org/abs/2205.10102v3'

function finding(
  overrides: Partial<Finding> = {},
  v: Partial<Finding['verification']> = {},
): Finding {
  return {
    statement: '该方法达到 38.36 dB',
    source_url: URL_B,
    evidence_quote: '38.36 dB',
    confidence: 0.9,
    verification: {
      status: 'verified',
      method: 'normalized_quote',
      source_content_hash: 'cd'.repeat(32),
      source_title: 'DAUHST',
      source_reference: 'Cai 等. DAUHST. NeurIPS, 2022. ' + URL_B,
      evidence_context: 'Our method achieves 38.36 dB on the KAIST benchmark.',
      reason: 'quote_found_in_source',
      semantic_status: 'supported',
      semantic_confidence: 0.92,
      semantic_reason: '原文数值与论断一致',
      claim_id: 'C-1',
      consistency_status: 'clear',
      contradicts_claim_ids: [],
      contradiction_reason: '',
      corroboration_status: 'corroborated',
      independent_source_count: 2,
      corroborates_claim_ids: ['C-9'],
      corroboration_reason: '两个独立发布方报告同一数值',
      quantity_label: 'PSNR = 38.36 dB',
      conditions_label: 'KAIST；10 scenes；28 波段',
      quantity_status: 'verified',
      ...v,
    },
    ...overrides,
  }
}

const MARKDOWN = '# 报告\n\n正文引用了 [1]。\n\n## 参考来源\n[1] ' + URL_B + '\n'

describe('PrintableReport：屏幕侧栏在纸上的等价物', () => {
  it('把全部证据一次性铺开，而不是像侧栏那样一次只显示一条', () => {
    render(
      <PrintableReport
        markdown={MARKDOWN}
        query="CASSI 重建方法对比"
        findings={[
          finding(),
          finding({ statement: '编码孔径为单色散结构', source_url: URL_B }, { claim_id: 'C-2' }),
        ]}
        citations={[URL_B]}
      />,
    )

    expect(screen.getByText('证据附录')).toBeInTheDocument()
    expect(screen.getByText(/该方法达到 38.36 dB/)).toBeInTheDocument()
    expect(screen.getByText(/编码孔径为单色散结构/)).toBeInTheDocument()
  })

  it('把原先只活在 tooltip 里的字段渲染为正式内容', () => {
    render(
      <PrintableReport markdown={MARKDOWN} query="q" findings={[finding()]} citations={[URL_B]} />,
    )

    expect(screen.getByText(/quote_found_in_source/)).toBeInTheDocument()
    expect(screen.getByText(/原文数值与论断一致/)).toBeInTheDocument()
    expect(screen.getByText(/两个独立发布方报告同一数值/)).toBeInTheDocument()
    expect(screen.getByText(/PSNR = 38.36 dB/)).toBeInTheDocument()
    expect(screen.getByText(/KAIST；10 scenes；28 波段/)).toBeInTheDocument()
    expect(screen.getByText(/置信度 92%/)).toBeInTheDocument()
    expect(screen.getByText(/已交叉印证 · 2 个独立来源/)).toBeInTheDocument()
  })

  it('打印完整快照哈希，不截断', () => {
    render(
      <PrintableReport markdown={MARKDOWN} query="q" findings={[finding()]} citations={[URL_B]} />,
    )

    // 截断的哈希无法用来核对快照，那就失去了留证的意义
    expect(screen.getByText(new RegExp('cd'.repeat(32)))).toBeInTheDocument()
  })

  it('在上下文中高亮逐字引文', () => {
    const { container } = render(
      <PrintableReport markdown={MARKDOWN} query="q" findings={[finding()]} citations={[URL_B]} />,
    )

    const mark = container.querySelector('mark')
    expect(mark?.textContent).toBe('38.36 dB')
  })

  it('参考来源用落库的学术引用，缺失时回退裸 URL', () => {
    render(
      <PrintableReport
        markdown={'正文 [1] [2]'}
        query="q"
        findings={[
          finding(),
          finding({ source_url: URL_A }, { source_reference: '', claim_id: 'C-3' }),
        ]}
        citations={[URL_B, URL_A]}
      />,
    )

    expect(screen.getAllByText(/Cai 等\. DAUHST\. NeurIPS, 2022\./).length).toBeGreaterThan(0)
    expect(screen.getAllByText(URL_A).length).toBeGreaterThan(0)
  })

  it('不把正文尾部自动追加的参考来源段落渲染两遍', () => {
    render(
      <PrintableReport markdown={MARKDOWN} query="q" findings={[finding()]} citations={[URL_B]} />,
    )

    expect(screen.getAllByText('参考来源')).toHaveLength(1)
  })

  it('保留正文中间提到「参考来源」的段落', () => {
    render(
      <PrintableReport
        markdown={'## 参考来源的可靠性\n\n本节讨论来源质量。\n\n## 结论\n\n完。'}
        query="q"
      />,
    )

    expect(screen.getByText('参考来源的可靠性')).toBeInTheDocument()
    expect(screen.getByText('本节讨论来源质量。')).toBeInTheDocument()
  })

  it('拦截数不可用时写明不可用，而不是 0', () => {
    render(
      <PrintableReport
        markdown={MARKDOWN}
        query="q"
        findings={[finding()]}
        citations={[URL_B]}
        blockedSources={null}
      />,
    )

    expect(screen.getByText(/不可用（本次事件流未含审计事件）/)).toBeInTheDocument()
  })

  it('拿到审计事件时显示拦截数', () => {
    render(
      <PrintableReport
        markdown={MARKDOWN}
        query="q"
        findings={[finding()]}
        citations={[URL_B]}
        blockedSources={3}
      />,
    )

    const row = screen.getByText('来源被拦截').closest('tr')
    expect(row?.textContent).toContain('3')
  })

  it('[n] 在纸上是文本标记，不是按钮', () => {
    const { container } = render(
      <PrintableReport markdown={MARKDOWN} query="q" findings={[finding()]} citations={[URL_B]} />,
    )

    expect(container.querySelector('button.cite-ref')).toBeNull()
    expect(container.querySelector('span.cite-ref')?.textContent).toBe('[1]')
  })

  it('冲突论断在纸上给出原因与对方 claim', () => {
    render(
      <PrintableReport
        markdown={'正文 [1]'}
        query="q"
        findings={[
          finding(
            {},
            {
              consistency_status: 'conflicted',
              contradicts_claim_ids: ['C-9', 'C-11'],
              contradiction_reason: '另一来源报告同一配置下为 37.21 dB',
            },
          ),
        ]}
        citations={[URL_B]}
      />,
    )

    expect(screen.getByText(/37\.21 dB/)).toBeInTheDocument()
    expect(screen.getByText(/参见 C-9、C-11/)).toBeInTheDocument()
  })

  it('屏幕态挂 print-only，预览态挂 print-preview', () => {
    const { container, rerender } = render(<PrintableReport markdown={MARKDOWN} query="q" />)
    expect(container.querySelector('.print-root')?.className).toContain('print-only')

    rerender(<PrintableReport markdown={MARKDOWN} query="q" preview />)
    expect(container.querySelector('.print-root')?.className).toContain('print-preview')
  })

  it('没有报告也能渲染，不崩', () => {
    render(<PrintableReport markdown="" query="尚未产出报告" />)

    expect(screen.getByText('尚未产出报告')).toBeInTheDocument()
    expect(screen.queryByText('证据附录')).toBeNull()
  })

  it('免责声明界定了系统到底声称了什么', () => {
    render(<PrintableReport markdown={MARKDOWN} query="q" />)

    expect(screen.getByText(/不保证论断在开放世界为真/)).toBeInTheDocument()
  })
})
