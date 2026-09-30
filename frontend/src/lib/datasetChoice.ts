import type { DatasetParseResult, DatasetSheet } from '../types'

export const DATASET_MAX_BYTES = 16 * 1024 * 1024

/** 已解析的数据文件与用户选定的工作表（多工作表时未选为 null）。 */
export interface DatasetChoice {
  parsed: DatasetParseResult
  sheet: string | null
}

export function chosenSheet(choice: DatasetChoice | null): DatasetSheet | null {
  if (!choice) return null
  if (choice.parsed.sheets.length === 1) return choice.parsed.sheets[0]
  return choice.parsed.sheets.find((item) => item.name === choice.sheet) ?? null
}
