import { render, screen } from '@testing-library/react'
import ReadingMeasurement from './ReadingMeasurement'

it('preserves the reported comparator, precision, uncertainty and experimental conditions', () => {
  render(
    <ReadingMeasurement
      records={[
        {
          evidence_id: 'e',
          quantity: {
            metric: 'PSNR',
            value: 0,
            rendered: '0.00',
            comparator: '>',
            uncertainty: 0.1,
            unit: 'dB',
          },
          conditions: { dataset: 'CAVE', bands: 31, split: 'test' },
        },
      ]}
    />,
  )
  expect(screen.getByText('原记录：PSNR > 0.00 ± 0.1 dB')).toBeInTheDocument()
  expect(screen.getByText('CAVE')).toBeInTheDocument()
  expect(screen.getByText('31')).toBeInTheDocument()
})

it('keeps missing values and unrecorded conditions distinct from zero or paper omissions', () => {
  render(
    <ReadingMeasurement
      records={[{ quantity: { metric: 'PSNR', value: null }, conditions: {} }]}
    />,
  )
  expect(screen.getByText(/数值未记录/)).toBeInTheDocument()
  expect(screen.getByText('实验条件尚未记录。')).toBeInTheDocument()
  expect(screen.getByText(/不表示原文未报告/)).toBeInTheDocument()
  expect(screen.queryByText(/PSNR = 0/)).not.toBeInTheDocument()
})
