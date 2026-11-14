import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api, type Schemas } from '@/api/client'

export type Source = Schemas['SourceOut']
export type Draft = Schemas['SourceInput']
export type ProbeResult = Schemas['ProbeResult']
export type SourceHealth = Schemas['SourceHealth']

function checked<T>(response: { data?: T; error?: { detail?: unknown }; response: Response }): T {
  if (!response.response.ok) {
    const detail = response.error?.detail
    throw new Error(
      Array.isArray(detail)
        ? detail.map((issue) => issue.loc.slice(1).join('.') + ' — ' + issue.msg).join('\n')
        : String(detail ?? 'Ошибка сервера ' + response.response.status),
    )
  }
  return response.data as T
}

export const useSourcesStore = defineStore('sources', () => {
  const sources = ref<Source[]>([])
  const types = ref<Schemas['SourceType'][]>([])
  const health = ref<Record<string, SourceHealth>>({})
  async function load() {
    const [rows, adapters, states] = await Promise.all([
      api.GET('/api/sources'),
      api.GET('/api/source-types'),
      api.GET('/api/sources/health'),
    ])
    sources.value = checked(rows)
    types.value = checked(adapters)
    health.value = Object.fromEntries(checked(states).map((h) => [h.source_id, h]))
  }
  async function resetBreaker(id: string) {
    const res = await api.POST('/api/sources/{source_id}/breaker/reset', { params: params(id) })
    if (!res.response.ok) throw new Error('Не удалось включить источник')
    await load()
  }
  function params(source_id: string) {
    return { path: { source_id } }
  }
  async function save(body: Draft, id?: string) {
    checked(
      id
        ? await api.PUT('/api/sources/{source_id}', { params: params(id), body })
        : await api.POST('/api/sources', { body }),
    )
    await load()
  }
  async function enable(id: string, enabled: boolean) {
    checked(
      await api.PATCH('/api/sources/{source_id}/enabled', {
        params: params(id),
        body: { enabled },
      }),
    )
    await load()
  }
  async function remove(id: string) {
    checked(await api.DELETE('/api/sources/{source_id}', { params: params(id) }))
    await load()
  }
  async function probe(query: string, draft: Draft, id?: string) {
    return checked(
      id
        ? await api.POST('/api/sources/{source_id}/probe', { params: params(id), body: { query } })
        : await api.POST('/api/sources/probe', {
            body: { query, type: draft.type, config: draft.config },
          }),
    )
  }
  return { sources, types, health, load, save, enable, remove, probe, resetBreaker }
})
