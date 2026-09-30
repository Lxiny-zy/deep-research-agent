import { render, screen } from '@testing-library/react'
import ContractPreview from './ContractPreview'
import type { TaskContract, TaskTemplate } from '../types'

const TEMPLATE = {
  key: 'paperRead',
  title: '论文精读',
  input_kind: 'paper',
} as TaskTemplate

function contract(extra: Partial<TaskContract> = {}): TaskContract {
  return {
    template: 'paperRead',
    title: '论文精读：x',
    original_request: 'x',
    focus: '',
    papers: [],
    dataset_csv: '',
    required_sections: [],
    deliverables: ['md'],
    constraints: [],
    evidence_rules: [],
    tier: 'standard',
    ...extra,
  } as TaskContract
}

function renderWith(value: TaskContract, attachmentCount = 0) {
  render(
    <ContractPreview
      template={TEMPLATE}
      contract={value}
      loading={false}
      error={null}
      attachmentCount={attachmentCount}
    />,
  )
}

describe('ContractPreview paper source', () => {
  it('never promises a topic search when no paper is given', () => {
    renderWith(contract())
    expect(screen.getByText(/还没有论文/)).toBeInTheDocument()
    expect(screen.queryByText(/按主题检索/)).not.toBeInTheDocument()
  })

  it('uses uploaded files as the paper', () => {
    renderWith(contract(), 2)
    expect(screen.getByText('使用上传的 2 个文件，不做开放检索')).toBeInTheDocument()
  })

  it('uses pasted paper text as the paper', () => {
    renderWith(contract({ pasted_paper_chars: 320 }))
    expect(screen.getByText(/使用粘贴的论文文本（约 320 字）/)).toBeInTheDocument()
  })
})
