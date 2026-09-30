import { render, screen } from '@testing-library/react'
import ContractPreview from './ContractPreview'
import type { DatasetSheetProfile, TaskContract, TaskTemplate } from '../types'

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

const DATA_TEMPLATE = {
  key: 'dataAnalysis',
  title: '数据分析',
  input_kind: 'dataset',
} as TaskTemplate
const PROFILE: DatasetSheetProfile = {
  name: 'CAVE',
  rows: 12,
  columns: [
    { name: 'method', type: '文本' },
    { name: 'psnr', type: '数值' },
  ],
  chars: 200,
}

function renderData(
  value: TaskContract,
  props: Partial<Parameters<typeof ContractPreview>[0]> = {},
) {
  render(
    <ContractPreview
      template={DATA_TEMPLATE}
      contract={value}
      loading={false}
      error={null}
      {...props}
    />,
  )
}

describe('ContractPreview dataset source', () => {
  it('asks for data instead of silently using synthetic data', () => {
    renderData(contract())
    expect(screen.getByText(/还没有数据/)).toBeInTheDocument()
    expect(screen.queryByText(/合成示例/)).not.toBeInTheDocument()
  })

  it('states the demo choice explicitly', () => {
    renderData(contract(), { demoData: true })
    expect(screen.getByText(/将用示例数据演示/)).toBeInTheDocument()
  })

  it('shows the uploaded sheet and that nothing is truncated', () => {
    renderData(contract(), {
      uploadedDataset: { filename: 'runs.xlsx', sheet: PROFILE, pending: false },
    })
    expect(
      screen.getByText('使用上传文件 runs.xlsx（工作表「CAVE」），12 行 × 2 列，完整使用不截断'),
    ).toBeInTheDocument()
  })

  it('asks to pick a sheet for multi-sheet files', () => {
    renderData(contract(), {
      uploadedDataset: { filename: 'runs.xlsx', sheet: null, pending: true },
    })
    expect(screen.getByText(/请先选择要分析的一张/)).toBeInTheDocument()
  })

  it('shows the pasted data profile or its parse error', () => {
    renderData(contract({ dataset_profile: PROFILE }))
    expect(screen.getByText(/粘贴的数据：12 行 × 2 列/)).toBeInTheDocument()
  })

  it('surfaces the parse error for pasted data', () => {
    renderData(contract({ dataset_error: '超过 1000000 字符上限' }))
    expect(screen.getByText('超过 1000000 字符上限')).toBeInTheDocument()
  })
})
