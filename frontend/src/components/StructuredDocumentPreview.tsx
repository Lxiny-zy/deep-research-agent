import type { ChartBlock, ReportBlock, ReportDocument, TableBlock, TableCell } from '../types'

// 结构化报告块的屏幕呈现：表格按原样渲染，图降级为指向源表的一节。
//
// 为什么图是降级而不是丢弃：ChartBlock 结构上必须指向一个 TableBlock
// （见 deep_research/report/document.py 的不变量），数字全在源表里，缺的只是
// 那层视觉编码。静默 filter 掉 chart 会让"文档里声明了一张图"这件事在屏幕上
// 完全不可见——读者不知道 PDF 里会多出什么。降级说明与后端 Markdown 渲染器
// （report/markdown.py 的 _chart_as_table）保持同一口径。

function cellFor(row: TableBlock['rows'][number], key: string): TableCell {
  return (
    row.cells[key] ?? {
      value: '',
      numeric: null,
      citations: [],
      note_ref: null,
      disputed: false,
    }
  )
}

/** 图与表在 blocks 里的相对顺序是有意义的，遍历时不能拆开重排。 */
function visualBlocks(document: ReportDocument): (TableBlock | ChartBlock)[] {
  return document.blocks.filter(
    (block): block is TableBlock | ChartBlock => block.kind === 'table' || block.kind === 'chart',
  )
}

function blockLabel(tables: number, charts: number): string {
  const parts: string[] = []
  if (tables > 0) parts.push(`${tables} 张表`)
  if (charts > 0) parts.push(`${charts} 张图`)
  return parts.join(' · ')
}

function isTable(block: ReportBlock): block is TableBlock {
  return block.kind === 'table'
}

function ChartFallback({ chart, document }: { chart: ChartBlock; document: ReportDocument }) {
  const source = document.blocks.find(
    (block): block is TableBlock => isTable(block) && block.id === chart.source_table,
  )
  // 源表不在文档里说明装配环节出了问题。这里不静默跳过，但也不谎称"缺一张图"：
  // 如实说明它指向的源表不可用，让问题在屏幕上可见。
  const sourceName = source ? source.title || source.id : ''
  return (
    <section className="structured-document-chart" data-testid={`structured-chart-${chart.id}`}>
      <h4>{chart.title || chart.id}</h4>
      <p className="muted small">
        {sourceName
          ? `此处不渲染矢量图形；该图的完整源数据见表《${sourceName}》。`
          : `该图指向的源表 ${chart.source_table} 不在本文档中。`}
      </p>
      {chart.caption && <p className="muted small">{chart.caption}</p>}
    </section>
  )
}

export default function StructuredDocumentPreview({
  document,
  print = false,
}: {
  document: ReportDocument
  print?: boolean
}) {
  const blocks = visualBlocks(document)
  if (blocks.length === 0) return null

  const tableCount = blocks.filter((block) => block.kind === 'table').length
  const chartCount = blocks.length - tableCount

  return (
    <section
      className={`structured-document-preview${print ? ' structured-document-print' : ''}`}
      data-testid="structured-document-preview"
      aria-label="结构化报告表格"
    >
      {!print && (
        <div className="structured-document-heading">
          <h3>结构化报告</h3>
          <span className="muted small">{blockLabel(tableCount, chartCount)}</span>
        </div>
      )}
      {blocks.map((block) =>
        block.kind === 'chart' ? (
          <ChartFallback chart={block} document={document} key={block.id} />
        ) : (
          <section
            className="structured-document-table"
            key={block.id}
            data-testid={`structured-table-${block.id}`}
          >
            <h4>{block.title || block.id}</h4>
            {block.caption && <p className="muted small">{block.caption}</p>}
            <div className="structured-document-table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>对象</th>
                    {block.columns.map((column) => (
                      <th
                        key={column.key}
                        className={
                          column.numeric || column.align === 'right' ? 'numeric' : undefined
                        }
                      >
                        {column.label || column.key}
                        {column.unit && <span className="muted small"> ({column.unit})</span>}
                        {/* 列脚注标记：注释本身在表下方列出，没有这个上标就无从
                            知道某一列对应哪条协议说明。 */}
                        {column.note_ref != null && (
                          <sup className="structured-document-note-ref">{column.note_ref}</sup>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, rowIndex) => (
                    <tr key={`${row.label}-${rowIndex}`}>
                      <th scope="row">{row.label || '未命名'}</th>
                      {block.columns.map((column) => {
                        const cell = cellFor(row, column.key)
                        const value = cell.value.trim() || '未报告'
                        return (
                          <td
                            key={column.key}
                            className={
                              column.numeric || column.align === 'right' ? 'numeric' : undefined
                            }
                            data-disputed={cell.disputed ? 'true' : undefined}
                          >
                            {value}
                            {cell.disputed && <sup title="存在争议">†</sup>}
                            {cell.note_ref != null && (
                              <sup className="structured-document-note-ref">{cell.note_ref}</sup>
                            )}
                            {cell.citations.length > 0 && (
                              <sup className="structured-document-citations">
                                [{cell.citations.join(', ')}]
                              </sup>
                            )}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {block.notes.length > 0 && (
              <ol className="structured-document-notes">
                {block.notes.map((note, index) => (
                  <li key={`${index}-${note}`} value={index + 1}>
                    {note}
                  </li>
                ))}
              </ol>
            )}
          </section>
        ),
      )}
    </section>
  )
}
