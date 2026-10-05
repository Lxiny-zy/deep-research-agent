import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import type { DatasetChoice } from '../lib/datasetChoice'
import type { DatasetParseResult } from '../types'
import DatasetPicker from './DatasetPicker'

const mocks = vi.hoisted(() => ({ parseDatasetFile: vi.fn(), mergeDatasetTables: vi.fn() }))
vi.mock('../api/client', () => mocks)

const parsed: DatasetParseResult = {
  filename: 'study.xlsx', skipped: [],
  sheets: [
    { name: 'Measurements', csv: 'id,mass\n1,10\n2,20', rows: 2, chars: 20,
      columns: [{ name: 'id', type: '文本' }, { name: 'mass', type: '数值' }] },
    { name: 'Subjects', csv: 'code,group\n1,A\n2,B', rows: 2, chars: 20,
      columns: [{ name: 'code', type: '文本' }, { name: 'group', type: '文本' }] },
  ],
}

function Harness() {
  const [value, setValue] = useState<DatasetChoice | null>({ parsed, sheet: null })
  return <>
    <DatasetPicker value={value} onChange={setValue} demo={false} onDemoChange={() => {}} />
    <output data-testid="choice">{JSON.stringify(value)}</output>
  </>
}

function chooseKeys(label: string, value: string) {
  const select = screen.getByLabelText(label) as HTMLSelectElement
  Array.from(select.options).forEach((option) => { option.selected = option.value === value })
  fireEvent.change(select)
}

it('previews an explicitly keyed join and retains the plan for task creation', async () => {
  mocks.mergeDatasetTables.mockResolvedValue({
    name: '合并结果', csv: 'id,mass,Subjects.group\n1,10,A\n2,20,B', rows: 2, chars: 45,
    columns: [], merge: { notes: ['主表 1 行未匹配'], tables: [], joins: [] },
  })
  render(<Harness />)
  fireEvent.click(screen.getByRole('button', { name: '合并多张工作表' }))
  fireEvent.change(screen.getByLabelText('连接表 第1步'), { target: { value: 'Subjects' } })
  chooseKeys('主表连接键 第1步', 'id')
  chooseKeys('右表连接键 第1步', 'code')
  fireEvent.click(screen.getByRole('button', { name: '预览合并结果' }))
  await screen.findByText('主表 1 行未匹配')
  expect(mocks.mergeDatasetTables).toHaveBeenCalledWith(expect.objectContaining({
    base: 'Measurements',
    joins: [expect.objectContaining({ sheet: 'Subjects', left_keys: ['id'], right_keys: ['code'] })],
  }), expect.any(AbortSignal))
  await waitFor(() => expect(screen.getByTestId('choice').textContent).toContain('Subjects.group'))
  fireEvent.change(screen.getByLabelText('保留范围 第1步'), { target: { value: 'left' } })
  expect(JSON.parse(screen.getByTestId('choice').textContent!).merge).toBeNull()
})

it('keeps failed joins unavailable for task creation and displays the reason', async () => {
  mocks.mergeDatasetTables.mockRejectedValue(new Error('右表连接键不唯一'))
  render(<Harness />)
  fireEvent.click(screen.getByRole('button', { name: '合并多张工作表' }))
  fireEvent.change(screen.getByLabelText('连接表 第1步'), { target: { value: 'Subjects' } })
  chooseKeys('主表连接键 第1步', 'id')
  chooseKeys('右表连接键 第1步', 'code')
  fireEvent.click(screen.getByRole('button', { name: '预览合并结果' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('右表连接键不唯一')
  expect(JSON.parse(screen.getByTestId('choice').textContent!).merge).toBeNull()
})

it('can replace a workbook when randomUUID is unavailable on an HTTP origin', async () => {
  vi.stubGlobal('crypto', { randomUUID: undefined })
  mocks.parseDatasetFile.mockResolvedValue({ ...parsed, filename: 'replacement.xlsx' })
  try {
    render(<Harness />)
    fireEvent.change(screen.getByLabelText('更换数据文件'), {
      target: { files: [new File(['x'], 'replacement.xlsx')] },
    })
    await waitFor(() => expect(screen.getByTestId('choice').textContent).toContain('replacement.xlsx'))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  } finally {
    vi.unstubAllGlobals()
  }
})
