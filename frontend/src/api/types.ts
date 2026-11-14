import type { Schemas } from './client'

export type CalculationDetail = Schemas['CalculationDetail']
export type CalculationSummary = Schemas['CalculationSummary']
export type Report = Schemas['Report']
export type PositionReport = Schemas['PositionReport']
export type OfferReport = Schemas['OfferReport']
export type VersionComparison = Schemas['VersionComparison']
export type PositionSpec = Schemas['PositionSpec']
export type SpecAttribute = Schemas['SpecAttribute']
export type AttrType = SpecAttribute['type']
export type Operator = NonNullable<SpecAttribute['operator']>

/** Событие прогресса из SSE /api/calculations/{id}/events */
export interface ProgressEvent {
  event: 'stage' | 'source' | 'position' | 'llm' | 'done' | 'failed' | 'cancelled'
  at: string
  stage?: string
  status?: string
  position?: number
  source?: string
  offers?: number
  duration_ms?: number
  error?: string | null
  message?: string
  batch?: number
  batches?: number
  total?: number
  sources?: string[]
  outcome?: string
  nmck?: string | null
  unit_price?: string | null
  included?: number
  excluded?: number
}
