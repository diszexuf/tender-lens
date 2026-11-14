const moneyFmt = new Intl.NumberFormat('ru-RU', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})
const numberFmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 4 })

export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  return `${moneyFmt.format(Number(value))} ₽`
}

export function num(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  return numberFmt.format(Number(value))
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' })
}

export function date(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleDateString('ru-RU')
}

interface Tone {
  label: string
  color: string
  icon: string
}

export const CALC_STATUS: Record<string, Tone> = {
  draft: { label: 'В очереди', color: 'info', icon: 'mdi-timer-sand' },
  extracting: { label: 'Разбор описания', color: 'info', icon: 'mdi-text-recognition' },
  spec_ready: { label: 'Проверка спецификации', color: 'warning', icon: 'mdi-playlist-edit' },
  collecting: { label: 'Сбор предложений', color: 'info', icon: 'mdi-web' },
  evaluating: { label: 'Оценка и расчёт', color: 'info', icon: 'mdi-calculator' },
  completed: { label: 'Завершён', color: 'success', icon: 'mdi-check-circle' },
  failed: { label: 'Ошибка', color: 'error', icon: 'mdi-alert-circle' },
  cancelled: { label: 'Отменён', color: 'grey', icon: 'mdi-cancel' },
  interrupted: { label: 'Прерван', color: 'warning', icon: 'mdi-pause-circle' },
}

/** Статусы, в которых идёт работа воркера: страница подписывается на события */
export const ACTIVE_STATUSES = ['draft', 'extracting', 'collecting', 'evaluating']
/** Статусы, в которых расчёт можно отменить (разбор описания не прерывается) */
export const CANCELLABLE_STATUSES = ['collecting', 'evaluating']

export const POSITION_STATUS: Record<string, Tone> = {
  ok: { label: 'Цена определена', color: 'success', icon: 'mdi-check-circle' },
  ok_adjusted: { label: 'Скорректированная выборка', color: 'success', icon: 'mdi-check-circle-outline' },
  heterogeneous: { label: 'Неоднородная выборка', color: 'warning', icon: 'mdi-chart-scatter-plot' },
  insufficient_data: { label: 'Недостаточно данных', color: 'error', icon: 'mdi-database-alert' },
}

export const OUTCOME: Record<string, Tone> = {
  full: { label: 'НМЦК определена', color: 'success', icon: 'mdi-check-decagram' },
  partial: { label: 'Неполный расчёт', color: 'warning', icon: 'mdi-alert' },
  none: { label: 'Цена не определена', color: 'error', icon: 'mdi-close-octagon' },
}

export const SOURCE_STATUS: Record<string, Tone> = {
  running: { label: 'Выполняется', color: 'info', icon: 'mdi-loading' },
  ok: { label: 'Успешно', color: 'success', icon: 'mdi-check' },
  empty: { label: 'Ничего не найдено', color: 'grey', icon: 'mdi-magnify-close' },
  error: { label: 'Ошибка', color: 'error', icon: 'mdi-alert-circle' },
  timeout: { label: 'Таймаут', color: 'error', icon: 'mdi-timer-alert' },
  structure_changed: { label: 'Структура изменилась', color: 'warning', icon: 'mdi-file-alert' },
  blocked: { label: 'Запрещено robots.txt', color: 'grey', icon: 'mdi-robot-off' },
  skipped: { label: 'Пропущен', color: 'grey', icon: 'mdi-skip-next' },
}

export const MATCH_STATUS: Record<string, Tone> = {
  match: { label: 'совпадает', color: 'success', icon: 'mdi-check' },
  mismatch: { label: 'не совпадает', color: 'error', icon: 'mdi-close' },
  unknown: { label: 'не указано', color: 'grey', icon: 'mdi-help' },
}

export const EXTRACTION: Record<string, string> = {
  jsonld: 'JSON-LD',
  microdata: 'microdata',
  selectors: 'селекторы',
  llm: 'LLM',
  api: 'API',
}

export const FLAGS: Record<string, string> = {
  penalties_unknown: 'нет сведений о неустойках',
  vat_unknown: 'НДС не указан',
  ktru_unconfirmed: 'код КТРУ не подтверждён',
  outlier_filter_skipped_small_n: 'фильтр выбросов не применялся (n < 5)',
  unit_assumed: 'единица не указана — принята единица позиции',
  pack_ambiguous: 'фасовка в названии, цена может быть за фасовку',
  pack_by_peers: 'фасовка выбрана по ценам других предложений',
  llm_extracted: 'данные извлечены LLM',
  llm_failed: 'LLM не смог извлечь характеристики',
}

export const OPERATORS: Record<string, string> = {
  eq: '=',
  gte: '≥',
  lte: '≤',
  in: 'одно из',
  between: 'от–до',
}

export const ATTR_TYPES: { value: string; title: string }[] = [
  { value: 'number', title: 'Число' },
  { value: 'range', title: 'Диапазон' },
  { value: 'enum', title: 'Значение из списка' },
  { value: 'bool', title: 'Да / нет' },
  { value: 'text', title: 'Текст' },
]

export function tone(map: Record<string, Tone>, key: string | null | undefined): Tone {
  return (key && map[key]) || { label: key ?? '—', color: 'grey', icon: 'mdi-help-circle' }
}
