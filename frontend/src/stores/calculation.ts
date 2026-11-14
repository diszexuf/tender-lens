import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api } from '@/api/client'
import type {
  CalculationDetail,
  PositionSpec,
  ProgressEvent,
  Report,
  VersionComparison,
} from '@/api/types'
import { ACTIVE_STATUSES } from '@/utils/format'

function errorText(error: unknown, fallback: string): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((d: { msg?: string; loc?: unknown[] }) => `${d.loc?.slice(1).join('.')}: ${d.msg}`).join('; ')
  }
  return fallback
}

export const useCalculationStore = defineStore('calculation', () => {
  const detail = ref<CalculationDetail | null>(null)
  const report = ref<Report | null>(null)
  const events = ref<ProgressEvent[]>([])
  const error = ref<string | null>(null)
  const busy = ref(false)
  let source: EventSource | null = null

  const isActive = computed(() => !!detail.value && ACTIVE_STATUSES.includes(detail.value.status))

  async function load(id: string): Promise<void> {
    error.value = null
    const { data, error: err } = await api.GET('/api/calculations/{calc_id}', {
      params: { path: { calc_id: id } },
    })
    if (!data) {
      error.value = errorText(err, 'Расчёт не найден')
      return
    }
    detail.value = data
    if (data.versions.length) await loadReport(id)
    if (ACTIVE_STATUSES.includes(data.status)) subscribe(id)
  }

  async function loadReport(id: string, version?: number): Promise<void> {
    const { data } = await api.GET('/api/calculations/{calc_id}/report', {
      params: { path: { calc_id: id }, query: version ? { version } : {} },
    })
    if (data) report.value = data
  }

  function subscribe(id: string): void {
    unsubscribe()
    events.value = []
    source = new EventSource(`/api/calculations/${id}/events`)
    source.onmessage = async (msg) => {
      const ev = JSON.parse(msg.data) as ProgressEvent
      events.value.push(ev)
      if (['done', 'failed', 'cancelled'].includes(ev.event)) {
        unsubscribe()
        await load(id)
      } else if (ev.event === 'stage' && ev.status !== 'started') {
        // Граница этапа: статус расчёта поменялся (спецификация готова, ошибка разбора и т. п.)
        await refreshDetail(id)
      }
    }
    source.onerror = () => {
      // Переподключение делает браузер; если расчёт уже завершён — обновим состояние
      void refreshDetail(id)
    }
  }

  async function refreshDetail(id: string): Promise<void> {
    const { data } = await api.GET('/api/calculations/{calc_id}', {
      params: { path: { calc_id: id } },
    })
    if (!data) return
    detail.value = data
    if (!ACTIVE_STATUSES.includes(data.status)) {
      unsubscribe()
      if (data.versions.length) await loadReport(id)
    }
  }

  function unsubscribe(): void {
    source?.close()
    source = null
  }

  async function create(text: string, region: string | null, autoRun: boolean): Promise<string | null> {
    busy.value = true
    error.value = null
    try {
      const { data, error: err } = await api.POST('/api/calculations', {
        body: { text, params: { region }, auto_run: autoRun },
      })
      if (!data) {
        error.value = errorText(err, 'Не удалось создать расчёт')
        return null
      }
      return data.id
    } finally {
      busy.value = false
    }
  }

  async function saveSpecAndRun(id: string, positions: PositionSpec[]): Promise<boolean> {
    busy.value = true
    error.value = null
    try {
      const saved = await api.PUT('/api/calculations/{calc_id}/spec', {
        params: { path: { calc_id: id } },
        body: { positions },
      })
      if (!saved.data) {
        error.value = errorText(saved.error, 'Спецификация не сохранена')
        return false
      }
      return await action(id, 'run')
    } finally {
      busy.value = false
    }
  }

  /** Правка спецификации завершённого расчёта: сервер сам запускает сбор или перерасчёт */
  async function saveSpecAfterRun(id: string, positions: PositionSpec[]): Promise<boolean> {
    busy.value = true
    error.value = null
    try {
      const { data, error: err } = await api.PUT('/api/calculations/{calc_id}/spec', {
        params: { path: { calc_id: id } },
        body: { positions },
      })
      if (!data) {
        error.value = errorText(err, 'Спецификация не сохранена')
        return false
      }
      detail.value = data
      subscribe(id)
      return true
    } finally {
      busy.value = false
    }
  }

  async function setDecision(
    id: string,
    offerId: string,
    decision: 'included' | 'excluded' | null,
    comment = '',
  ): Promise<boolean> {
    error.value = null
    const params = { params: { path: { calc_id: id, offer_id: offerId } } }
    const res = decision
      ? await api.PUT('/api/calculations/{calc_id}/offers/{offer_id}/decision', {
          ...params,
          body: { decision, comment },
        })
      : await api.DELETE('/api/calculations/{calc_id}/offers/{offer_id}/decision', params)
    if (!res.data) {
      error.value = errorText(res.error, 'Решение не сохранено')
      return false
    }
    detail.value = res.data
    subscribe(id)
    return true
  }

  async function loadVersions(id: string): Promise<VersionComparison[]> {
    const { data, error: err } = await api.GET('/api/calculations/{calc_id}/versions', {
      params: { path: { calc_id: id } },
    })
    if (!data) error.value = errorText(err, 'Версии не загружены')
    return data ?? []
  }

  async function action(id: string, kind: 'run' | 'recalculate' | 'cancel'): Promise<boolean> {
    error.value = null
    const path = { params: { path: { calc_id: id } } }
    const res =
      kind === 'run'
        ? await api.POST('/api/calculations/{calc_id}/run', { ...path, body: { use_cache: true } })
        : kind === 'recalculate'
          ? await api.POST('/api/calculations/{calc_id}/recalculate', path)
          : await api.POST('/api/calculations/{calc_id}/cancel', path)
    if (!res.data) {
      error.value = errorText(res.error, 'Действие не выполнено')
      return false
    }
    detail.value = res.data
    if (kind !== 'cancel') subscribe(id)
    return true
  }

  function reset(): void {
    unsubscribe()
    detail.value = null
    report.value = null
    events.value = []
    error.value = null
  }

  return {
    detail,
    report,
    events,
    error,
    busy,
    isActive,
    load,
    loadReport,
    create,
    saveSpecAndRun,
    saveSpecAfterRun,
    setDecision,
    loadVersions,
    action,
    reset,
  }
})
