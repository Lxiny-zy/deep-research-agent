import { useState } from 'react'
import ReportView from '../components/ReportView'
import ReportActions from '../components/ReportActions'
import type { ChartBlock, Finding, ReportDocument, TableBlock } from '../types'

const markdown =
  '# 科研内容验收样例\n\n测量关系 $E=mc^2$。原始观测包含负值、缺失值与两个独立系列 [1]。'
const url = 'https://example.invalid/scientific-fixture'
const table: TableBlock = {
  kind: 'table',
  id: 'observations',
  title: '观测数据与公式',
  caption: '数值保留原始精度；引用 [1]，未映射引用 [9]。',
  notes: ['缺失值表示未报告，不能解释为零。'],
  columns: [
    {
      key: 'a',
      label: '系列 A：$\\Delta E$',
      unit: 'meV',
      numeric: true,
      align: 'right',
      note_ref: 1,
    },
    {
      key: 'b',
      label: '系列 B：$\\sigma^2$',
      unit: 'meV',
      numeric: true,
      align: 'right',
      note_ref: null,
    },
    {
      key: 'formula',
      label: '模型与条件',
      unit: '',
      numeric: false,
      align: 'left',
      note_ref: null,
    },
  ],
  rows: [
    {
      label: '样品 Alpha',
      citation: 1,
      cells: {
        a: {
          value: '$-2.50\\pm0.10$',
          numeric: -2.5,
          citations: [1],
          note_ref: null,
          disputed: false,
        },
        b: { value: '3.20', numeric: 3.2, citations: [1], note_ref: null, disputed: false },
        formula: {
          value: '$E=mc^2$，$T=273.15\\,\\mathrm{K}$',
          numeric: null,
          citations: [],
          note_ref: null,
          disputed: false,
        },
      },
    },
    {
      label: '样品 Beta',
      citation: null,
      cells: {
        a: { value: '1.25', numeric: 1.25, citations: [1], note_ref: null, disputed: false },
        b: { value: '', numeric: null, citations: [], note_ref: null, disputed: false },
      },
    },
    {
      label: '样品 Gamma',
      citation: 1,
      cells: {
        a: { value: '4.50', numeric: 4.5, citations: [1], note_ref: null, disputed: false },
        b: { value: '-1.75', numeric: -1.75, citations: [1], note_ref: null, disputed: true },
      },
    },
  ],
}
const chart: ChartBlock = {
  kind: 'chart',
  id: 'multi-series',
  title: '多系列观测图',
  form: 'grouped_bar',
  source_table: table.id,
  value_columns: ['a', 'b'],
  x_column: '',
  emphasis: '',
  y_label: '能量差 / meV',
  caption: '图形取值严格来自表格 numeric 字段。数据证据 [1]。',
}
const finding: Finding = {
  statement: '原始观测包括 -2.50 meV 与 3.20 meV。',
  source_url: url,
  evidence_quote: 'Alpha: -2.50 ± 0.10 meV; B: 3.20 meV.',
  confidence: 1,
  verification: {
    status: 'verified',
    method: 'normalized_quote',
    source_content_hash: 'a'.repeat(64),
    source_title: '观测记录',
    evidence_context:
      'Observation log. Alpha: -2.50 ± 0.10 meV; B: 3.20 meV. Missing means unreported.',
    reason: '',
    semantic_status: 'supported',
    semantic_confidence: 1,
    semantic_reason: '',
    claim_id: 'fixture-claim',
    consistency_status: 'clear',
    contradicts_claim_ids: [],
    contradiction_reason: '',
    corroboration_status: 'single_source',
    independent_source_count: 1,
    corroborates_claim_ids: [],
    corroboration_reason: '',
  },
}
const document: ReportDocument = {
  schema_version: 1,
  query: '科研验收',
  content_version: 'a'.repeat(64),
  source_version: 'b'.repeat(64),
  blocks: [{ kind: 'prose', markdown }, table, chart],
  references: [{ index: 1, url, reference: '观测记录' }],
  evidence: [],
  overview: {
    records: 1,
    verbatim_matched: 1,
    semantically_supported: 1,
    corroborated: 0,
    conflicted: 0,
    blocked_sources: 0,
  },
  disclaimer: '本地验收合成数据，非研究结论。',
}

/** Development-only fixture using production components, with no external research requests. */
export default function ScientificDocumentPreviewPage() {
  const [form, setForm] = useState<ChartBlock['form']>('grouped_bar')
  const selected = {
    ...document,
    blocks: [
      { kind: 'prose' as const, markdown },
      table,
      { ...chart, form, ...(form === 'scatter' ? { x_column: 'a', value_columns: ['b'] } : {}) },
    ],
  }
  return (
    <main style={{ maxWidth: 1100, padding: 20, margin: '0 auto' }}>
      <p>本地验收样例 · 真实组件 · 合成科学数据</p>
      <label>
        图形类型{' '}
        <select
          value={form}
          onChange={(event) => setForm(event.target.value as ChartBlock['form'])}
        >
          {['bar', 'dot', 'grouped_bar', 'scatter', 'line'].map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
      </label>
      <ReportActions
        markdown={markdown}
        query="科研验收"
        runId="scientific-fixture"
        documentReady
        contentVersion={document.content_version}
        capabilities={{ pdf: true, xlsx: true }}
        tableOptions={[{ id: table.id, label: table.title }]}
      />
      <ReportView
        markdown={markdown}
        streaming={false}
        document={selected}
        citations={[url]}
        findings={[finding]}
      />
    </main>
  )
}
