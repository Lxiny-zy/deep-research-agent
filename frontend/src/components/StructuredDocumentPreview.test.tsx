import { render, screen, within } from '@testing-library/react'
import type { ReportDocument } from '../types'
import StructuredDocumentPreview from './StructuredDocumentPreview'

// 这个组件的风险不是"表画得好不好看"，而是**默默丢东西**：
// 它先前只 filter 出 table，文档里声明的 chart 在屏幕上完全不可见，
// 而后端 PDF 会把那张图渲染出来——同一份 run 在两种输出里内容不同。

function doc(over: Partial<ReportDocument> = {}): ReportDocument {
  return {
    schema_version: 1,
    query: 'q',
    blocks: [],
    references: [],
    evidence: [],
    overview: {
      records: 0,
      verbatim_matched: 0,
      semantically_supported: 0,
      corroborated: 0,
      conflicted: 0,
      blocked_sources: null,
    },
    disclaimer: '',
    ...over,
  }
}

const TABLE: ReportDocument['blocks'][number] = {
  kind: 'table',
  id: 'recon',
  title: '重建算法',
  columns: [{ key: 'psnr', label: 'PSNR', unit: 'dB', align: 'right', numeric: true, note_ref: 1 }],
  rows: [
    {
      label: 'DAUHST',
      citation: 1,
      cells: {
        psnr: { value: '38.36', numeric: 38.36, citations: [1], note_ref: 2, disputed: true },
      },
    },
  ],
  notes: ['28 波段，KAIST 10 scenes', '作者自报，未经复现'],
  caption: '',
}

const CHART: ReportDocument['blocks'][number] = {
  kind: 'chart',
  id: 'recon-bar',
  title: 'PSNR 对比',
  form: 'bar',
  source_table: 'recon',
  value_columns: ['psnr'],
  x_column: '',
  emphasis: '',
  y_label: 'dB',
  caption: '柱长即数值，基线为零。',
}

describe('StructuredDocumentPreview 图块降级', () => {
  it('图不被静默丢弃，而是降级成指向源表的一节', () => {
    render(<StructuredDocumentPreview document={doc({ blocks: [TABLE, CHART] })} />)

    const chart = screen.getByTestId('structured-chart-recon-bar')
    expect(within(chart).getByText('PSNR 对比')).toBeInTheDocument()
    // 说明必须指向源表的标题，读者才知道去哪看数字
    expect(within(chart).getByText(/《重建算法》/)).toBeInTheDocument()
    expect(within(chart).getByText('柱长即数值，基线为零。')).toBeInTheDocument()
  })

  it('源表缺失时如实说明，而不是谎称"这里缺一张图"', () => {
    render(<StructuredDocumentPreview document={doc({ blocks: [CHART] })} />)

    expect(screen.getByText(/源表 recon 不在本文档中/)).toBeInTheDocument()
  })

  it('计数把图和表分开报，不把图算成表', () => {
    render(<StructuredDocumentPreview document={doc({ blocks: [TABLE, CHART] })} />)

    expect(screen.getByText('1 张表 · 1 张图')).toBeInTheDocument()
  })

  it('只有图没有表时也渲染——否则整块内容凭空消失', () => {
    render(<StructuredDocumentPreview document={doc({ blocks: [CHART] })} />)

    expect(screen.getByTestId('structured-document-preview')).toBeInTheDocument()
  })

  it('没有任何图表块时不渲染空壳', () => {
    const { container } = render(
      <StructuredDocumentPreview
        document={doc({ blocks: [{ kind: 'prose', markdown: '正文' }] })}
      />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})

describe('StructuredDocumentPreview 脚注与单元格标记', () => {
  it('脚注编号可对应：notes 有序号，列与单元格带上标', () => {
    render(<StructuredDocumentPreview document={doc({ blocks: [TABLE] })} />)

    const table = screen.getByTestId('structured-table-recon')
    // 协议脚注本身
    expect(within(table).getByText('28 波段，KAIST 10 scenes')).toBeInTheDocument()
    // note_ref 上标（列 1 / 单元格 2）——没有它就无从知道哪条注对应哪列
    const refs = within(table).getAllByText(/^[12]$/)
    expect(refs.length).toBeGreaterThanOrEqual(2)
  })

  it('争议单元格带标记，引用号照常呈现', () => {
    render(<StructuredDocumentPreview document={doc({ blocks: [TABLE] })} />)

    const table = screen.getByTestId('structured-table-recon')
    expect(within(table).getByTitle('存在争议')).toBeInTheDocument()
    expect(within(table).getByText('[1]')).toBeInTheDocument()
  })

  it('缺值写「未报告」，不写 0 或空白', () => {
    const sparse = {
      ...TABLE,
      rows: [{ label: 'X', citation: null, cells: {} }],
    } as ReportDocument['blocks'][number]
    render(<StructuredDocumentPreview document={doc({ blocks: [sparse] })} />)

    expect(screen.getByText('未报告')).toBeInTheDocument()
  })
})
