import { render, screen, within } from '@testing-library/react'
import type { ReportDocument, TableBlock } from '../types'
import StructuredDocumentPreview from './StructuredDocumentPreview'
import ReportView from './ReportView'
import userEvent from '@testing-library/user-event'

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

describe('StructuredDocumentPreview 图形与数据后备', () => {
  it('从源表实际绘制图形，并保留可访问的数据表', () => {
    render(<StructuredDocumentPreview document={doc({ blocks: [TABLE, CHART] })} />)

    const chart = screen.getByTestId('structured-chart-recon-bar')
    expect(within(chart).getByRole('heading', { name: 'PSNR 对比' })).toBeInTheDocument()
    expect(within(chart).getByRole('img')).toBeInTheDocument()
    expect(chart.querySelector('rect')).toHaveAttribute('height')
    expect(within(chart).getByRole('table')).toBeInTheDocument()
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

it('typesets formulas and preserves negative values, units and missing values in grouped graphs', () => {
  const table: TableBlock = {
    ...TABLE,
    columns: [TABLE.columns[0], { ...TABLE.columns[0], key: 'other', label: '$\\sigma^2$' }],
    rows: [
      {
        label: 'A',
        citation: null,
        cells: {
          psnr: {
            value: '$-2.5\\pm0.1$',
            numeric: -2.5,
            citations: [1],
            note_ref: null,
            disputed: false,
          },
          other: { value: '3.2', numeric: 3.2, citations: [], note_ref: null, disputed: false },
        },
      },
      {
        label: 'B',
        citation: null,
        cells: {
          psnr: { value: '', numeric: null, citations: [], note_ref: null, disputed: false },
        },
      },
    ],
  }
  const { container } = render(
    <StructuredDocumentPreview
      document={doc({
        blocks: [table, { ...CHART, form: 'grouped_bar', value_columns: ['psnr', 'other'] }],
      })}
    />,
  )
  expect(container.querySelectorAll('.katex').length).toBeGreaterThan(2)
  expect(container.querySelectorAll('svg rect')).toHaveLength(2)
  expect(container.querySelector('[data-value="-2.5"]')).toBeInTheDocument()
  expect(screen.getAllByText('未报告').length).toBeGreaterThan(0)
  expect(container.querySelectorAll('svg [data-value="0"]')).toHaveLength(0)
})

it('does not bridge a missing observation in a line chart', () => {
  const table = {
    ...TABLE,
    rows: [
      TABLE.rows[0],
      { label: 'Missing', citation: null, cells: {} },
      { ...TABLE.rows[0], label: 'Last' },
    ],
  }
  const { container } = render(
    <StructuredDocumentPreview document={doc({ blocks: [table, { ...CHART, form: 'line' }] })} />,
  )
  expect(container.querySelector('svg path')?.getAttribute('d')?.match(/M/g)).toHaveLength(2)
  expect(container.querySelector('svg path')?.getAttribute('d')).not.toContain('L')
})

it('opens the same evidence drawer by keyboard for table and chart citations; missing mappings stay inert', async () => {
  const document = doc({
    blocks: [TABLE, { ...CHART, caption: '图注 [1] 与缺失映射 [9]。' }],
    references: [{ index: 1, url: 'https://example.test/paper', reference: '原始论文' }],
  })
  render(
    <ReportView
      markdown="正文 [1]。"
      streaming={false}
      citations={['https://example.test/paper']}
      document={document}
    />,
  )
  const table = screen.getByTestId('structured-table-recon')
  const button = within(table).getByRole('button', { name: '查看引用 1 的证据' })
  button.focus()
  await userEvent.keyboard('{Enter}')
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(screen.getByText(/没有可呈现的证据|暂无|未.*绑定|没有.*证据/)).toBeInTheDocument()
  const chart = screen.getByTestId('structured-chart-recon-bar')
  expect(within(chart).queryByRole('button', { name: /引用 9/ })).not.toBeInTheDocument()
  expect(within(chart).getByTitle('引用映射不可用，无法定位证据')).toHaveTextContent('[9]')
})
