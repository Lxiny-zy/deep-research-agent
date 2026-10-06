import { useId, useMemo, type ReactNode } from 'react'
import Markdown, { type Components } from 'react-markdown'
import type { ChartBlock, ReportDocument, TableBlock, TableCell } from '../types'
import { citationLocations, documentNumber } from '../lib/bibliography'
import { remarkCitations } from '../lib/evidence'
import { mathRemarkPlugins, mathRehypePlugins, normalizeMathMarkdown } from '../lib/scientificMath'

export type CitationRenderer = (locations: number[], children: ReactNode) => ReactNode
interface ContentProps {
  document: ReportDocument
  renderCitation?: CitationRenderer
}

function ScientificText({ text, document, renderCitation }: ContentProps & { text: string }) {
  const documents = Object.fromEntries(
    (document.bibliography?.locations ?? []).map((item) => [item.index, item.document]),
  )
  const components = useMemo<Components>(
    () => ({
      p: ({ children }) => <span>{children}</span>,
      a: ({ href, children }) =>
        href?.startsWith('#cite-') ? (
          <>
            {renderCitation?.(citationLocations(href), children) ?? (
              <span className="cite-ref inert" title="引用映射不可用，无法定位证据">
                {children}
              </span>
            )}
          </>
        ) : (
          <a href={href} rel="noreferrer" target="_blank">
            {children}
          </a>
        ),
    }),
    [renderCitation],
  )
  return (
    <Markdown
      remarkPlugins={[...mathRemarkPlugins, [remarkCitations, { documents }]]}
      rehypePlugins={mathRehypePlugins}
      components={components}
    >
      {normalizeMathMarkdown(text)}
    </Markdown>
  )
}

function Citations({
  locations,
  document,
  renderCitation,
}: ContentProps & { locations: number[] }) {
  if (!locations.length) return null
  const label = `[${[...new Set(locations.map((n) => documentNumber(document.bibliography ?? undefined, n)))].join(', ')}]`
  return (
    <sup className="structured-document-citations">
      {renderCitation?.(locations, label) ?? (
        <span className="cite-ref inert" title="引用映射不可用，无法定位证据">
          {label}
        </span>
      )}
    </sup>
  )
}

function cellFor(row: TableBlock['rows'][number], key: string): TableCell {
  return (
    row.cells[key] ?? { value: '', numeric: null, citations: [], note_ref: null, disputed: false }
  )
}

function DataTable({ table, ...content }: ContentProps & { table: TableBlock }) {
  return (
    <>
      <p className="structured-scroll-hint muted small">
        横向滑动，或聚焦表格后按方向键，查看完整数据与引用。
      </p>
      <div
        className="structured-document-table-scroll"
        tabIndex={0}
        role="region"
        aria-label={`${table.title || table.id} 数据表，可横向滚动`}
      >
        <table>
          <caption className="sr-only">{table.title || table.id}：原始数值、单位与引用</caption>
          <thead>
            <tr>
              <th scope="col">对象</th>
              {table.columns.map((column) => (
                <th
                  scope="col"
                  key={column.key}
                  className={column.numeric || column.align === 'right' ? 'numeric' : undefined}
                >
                  <ScientificText text={column.label || column.key} {...content} />
                  {column.unit && (
                    <span className="muted small">
                      {' '}
                      (<ScientificText text={column.unit} {...content} />)
                    </span>
                  )}
                  {column.note_ref != null && (
                    <sup className="structured-document-note-ref">{column.note_ref}</sup>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, index) => (
              <tr key={index}>
                <th scope="row">
                  <ScientificText text={row.label || '未命名'} {...content} />
                  {row.citation != null &&
                    !Object.values(row.cells).some((cell) =>
                      cell.citations.includes(row.citation!),
                    ) && <Citations locations={[row.citation]} {...content} />}
                </th>
                {table.columns.map((column) => {
                  const cell = cellFor(row, column.key)
                  return (
                    <td
                      key={column.key}
                      className={column.numeric || column.align === 'right' ? 'numeric' : undefined}
                      data-disputed={cell.disputed ? 'true' : undefined}
                    >
                      <ScientificText text={cell.value.trim() || '未报告'} {...content} />
                      {cell.disputed && <sup title="存在争议">†</sup>}
                      {cell.note_ref != null && (
                        <sup className="structured-document-note-ref">{cell.note_ref}</sup>
                      )}
                      <Citations locations={cell.citations} {...content} />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.notes.length > 0 && (
        <ol className="structured-document-notes">
          {table.notes.map((note, i) => (
            <li key={i} value={i + 1}>
              <ScientificText text={note} {...content} />
            </li>
          ))}
        </ol>
      )}
    </>
  )
}

const COLORS = ['#146b8c', '#a24720', '#6d4ca0', '#25754b', '#a33a6c', '#74621b']
const finite = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value)

/** Only typed numeric source values are drawn. Missing values never become zero or interpolated lines. */
function Chart({ chart, ...content }: ContentProps & { chart: ChartBlock }) {
  const id = useId()
  const source = content.document.blocks.find(
    (block): block is TableBlock => block.kind === 'table' && block.id === chart.source_table,
  )
  const columns = chart.value_columns
    .map((key) => source?.columns.find((col) => col.key === key))
    .filter((col) => col != null)
  const quantitativeX =
    chart.form === 'scatter' || (chart.form === 'line' && Boolean(chart.x_column))
  const points =
    source?.rows.flatMap((row, r) =>
      columns.flatMap((column, s) => {
        const cell = cellFor(row, column.key)
        const x = quantitativeX ? row.cells[chart.x_column]?.numeric : r
        return finite(cell.numeric) && finite(x)
          ? [{ row, r, s, column, cell, x, y: cell.numeric }]
          : []
      }),
    ) ?? []
  const units = new Set(columns.map((column) => column.unit))
  const invalid = !source
    ? `该图指向的源表 ${chart.source_table} 不在本文档中。`
    : columns.length !== chart.value_columns.length || !columns.length
      ? '图形所需数值列不可用，请核对源数据表。'
      : units.size > 1
        ? '数值列的单位不同，无法在同一数值轴上比较；请核对源数据表。'
        : !points.length
          ? '没有可绘制的数值；缺失值保留在源数据表中。'
          : ''
  const width = 760,
    height = 390,
    left = 75,
    right = 25,
    top = 25,
    bottom = 95
  const plotW = width - left - right,
    plotH = height - top - bottom
  const bar = chart.form === 'bar' || chart.form === 'grouped_bar'
  const minY = Math.min(...points.map((p) => p.y), ...(bar ? [0] : []))
  const maxY = Math.max(...points.map((p) => p.y), ...(bar ? [0] : []))
  const padding = maxY === minY ? Math.max(Math.abs(maxY) * 0.1, 1) : (maxY - minY) * 0.08
  const low = minY - (bar && minY === 0 ? 0 : padding),
    high = maxY + padding
  const minX = Math.min(...points.map((p) => p.x)),
    maxX = Math.max(...points.map((p) => p.x))
  const xAt = (x: number) =>
    quantitativeX
      ? left + (maxX === minX ? plotW / 2 : ((x - minX) / (maxX - minX)) * plotW)
      : left + ((x + 0.5) * plotW) / Math.max(source?.rows.length ?? 0, 1)
  const yAt = (y: number) => top + ((high - y) / (high - low)) * plotH
  const groupWidth = (plotW / Math.max(source?.rows.length ?? 0, 1)) * 0.72
  const barWidth = groupWidth / Math.max(columns.length, 1)
  return (
    <section className="structured-document-chart" data-testid={`structured-chart-${chart.id}`}>
      <h4>
        <ScientificText text={chart.title || chart.id} {...content} />
      </h4>
      {invalid ? (
        <p className="muted small" role="status">
          {invalid}
        </p>
      ) : (
        <>
          <p className="structured-scroll-hint muted small">
            横向滑动，或聚焦图形后按方向键，查看全部数据组。
          </p>
          <div
            className="structured-chart-scroll"
            role="region"
            tabIndex={0}
            aria-label={`${chart.title || chart.id} 图形，可横向滚动`}
          >
            <svg
              className="structured-chart-svg"
              viewBox={`0 0 ${width} ${height}`}
              role="img"
              aria-labelledby={`${id}-title ${id}-desc`}
            >
              <title id={`${id}-title`}>{chart.title || chart.id}</title>
              <desc
                id={`${id}-desc`}
              >{`${chart.form}；${points.length} 个数据点。数值、单位、缺失值和证据引用见下方源数据表。`}</desc>
              {Array.from({ length: 5 }, (_, i) => low + ((high - low) * i) / 4).map((tick) => (
                <g key={tick}>
                  <line
                    x1={left}
                    x2={width - right}
                    y1={yAt(tick)}
                    y2={yAt(tick)}
                    className="structured-chart-grid"
                  />
                  <text x={left - 8} y={yAt(tick) + 4} textAnchor="end">
                    {Number(tick.toPrecision(4))}
                  </text>
                </g>
              ))}
              <line
                x1={left}
                x2={width - right}
                y1={yAt(bar ? 0 : low)}
                y2={yAt(bar ? 0 : low)}
                stroke="currentColor"
              />
              {chart.form === 'line' &&
                columns.map((column, s) => {
                  let path = '',
                    previous = -2
                  for (const p of points.filter((point) => point.s === s)) {
                    path += `${p.r === previous + 1 ? 'L' : 'M'}${xAt(p.x)},${yAt(p.y)} `
                    previous = p.r
                  }
                  return (
                    <path
                      key={column.key}
                      d={path}
                      fill="none"
                      stroke={COLORS[s % COLORS.length]}
                      strokeWidth={2}
                      strokeDasharray={s % 2 ? '6 3' : undefined}
                    />
                  )
                })}
              {points.map((p) => (
                <g key={`${p.r}-${p.s}`} data-value={p.y}>
                  <title>{`${p.row.label} · ${p.column.label}: ${p.cell.value} ${p.column.unit}`}</title>
                  {bar ? (
                    <rect
                      x={xAt(p.x) - groupWidth / 2 + p.s * barWidth}
                      y={Math.min(yAt(p.y), yAt(0))}
                      width={Math.max(barWidth - 2, 1)}
                      height={Math.abs(yAt(p.y) - yAt(0))}
                      fill={COLORS[p.s % COLORS.length]}
                    />
                  ) : (
                    <circle
                      cx={xAt(p.x)}
                      cy={yAt(p.y)}
                      r={4 + (p.s % 3)}
                      fill={COLORS[p.s % COLORS.length]}
                      stroke="white"
                    />
                  )}
                </g>
              ))}
              {quantitativeX
                ? [minX, ...(minX !== maxX ? [maxX] : [])].map((x) => (
                    <text key={x} x={xAt(x)} y={height - bottom + 20} textAnchor="middle">
                      {x}
                    </text>
                  ))
                : source!.rows.map((row, r) => (
                    <text
                      key={r}
                      transform={`translate(${xAt(r)},${height - bottom + 18}) rotate(-25)`}
                      textAnchor="end"
                    >
                      {row.label}
                    </text>
                  ))}
            </svg>
          </div>
          <p className="structured-chart-axis">
            <ScientificText text={chart.y_label || columns[0]?.unit || '数值'} {...content} />
            {quantitativeX && (
              <>
                {' '}
                · 横轴：
                <ScientificText
                  text={`${source!.columns.find((c) => c.key === chart.x_column)?.label ?? chart.x_column} (${source!.columns.find((c) => c.key === chart.x_column)?.unit ?? ''})`}
                  {...content}
                />
              </>
            )}
          </p>
          <ul className="structured-chart-legend" aria-label="图形数据系列">
            {columns.map((column, s) => (
              <li key={column.key}>
                <span style={{ background: COLORS[s % COLORS.length] }} aria-hidden="true" />
                <ScientificText
                  text={`${column.label}${column.unit ? ` (${column.unit})` : ''}`}
                  {...content}
                />
              </li>
            ))}
          </ul>
        </>
      )}
      {chart.caption && (
        <p className="muted small">
          <ScientificText text={chart.caption} {...content} />
        </p>
      )}
      {source && (
        <details className="structured-chart-data" open>
          <summary>源数据表《{source.title || source.id}》：数值与证据</summary>
          <DataTable table={source} {...content} />
        </details>
      )}
    </section>
  )
}

export default function StructuredDocumentPreview({
  document,
  print = false,
  renderCitation,
}: ContentProps & { print?: boolean }) {
  const blocks = document.blocks.filter(
    (block): block is TableBlock | ChartBlock => block.kind === 'table' || block.kind === 'chart',
  )
  if (!blocks.length) return null
  const tables = blocks.filter((block) => block.kind === 'table').length
  const charts = blocks.length - tables
  const content = { document, renderCitation }
  return (
    <section
      className={`structured-document-preview${print ? ' structured-document-print' : ''}`}
      data-testid="structured-document-preview"
      aria-label="结构化报告表格与图形"
    >
      {!print && (
        <div className="structured-document-heading">
          <h3>结构化报告</h3>
          <span className="muted small">
            {[tables && `${tables} 张表`, charts && `${charts} 张图`].filter(Boolean).join(' · ')}
          </span>
        </div>
      )}
      {blocks.map((block) =>
        block.kind === 'chart' ? (
          <Chart key={block.id} chart={block} {...content} />
        ) : (
          <section
            key={block.id}
            className="structured-document-table"
            data-testid={`structured-table-${block.id}`}
          >
            <h4>
              <ScientificText text={block.title || block.id} {...content} />
            </h4>
            {block.caption && (
              <p className="muted small">
                <ScientificText text={block.caption} {...content} />
              </p>
            )}
            <DataTable table={block} {...content} />
          </section>
        ),
      )}
    </section>
  )
}
