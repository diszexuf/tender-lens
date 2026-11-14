import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type Schemas } from '@/api/client'

export type SettingsOut = Schemas['SettingsOut']
export type Unit = Schemas['UnitIn']
export type Synonym = Schemas['SynonymOut']
export type PriceIndex = Schemas['PriceIndexItem']
export type SettingsVersion = Schemas['SettingsVersionOut']

function checked<T>(response: { data?: T; error?: unknown; response: Response }): T {
  if (!response.response.ok) {
    const detail = (response.error as { detail?: unknown } | undefined)?.detail
    throw new Error(
      Array.isArray(detail)
        ? detail.map((d: { loc?: unknown[]; msg?: string }) => `${d.loc?.slice(1).join('.')}: ${d.msg}`).join('; ')
        : String(detail ?? `Ошибка сервера ${response.response.status}`),
    )
  }
  return response.data as T
}

export const useSettingsStore = defineStore('settings', () => {
  const settings = ref<SettingsOut | null>(null)
  const versions = ref<SettingsVersion[]>([])
  const units = ref<Unit[]>([])
  const synonyms = ref<Synonym[]>([])
  const indexes = ref<PriceIndex[]>([])

  async function load(): Promise<void> {
    const [s, v, u, syn, idx] = await Promise.all([
      api.GET('/api/settings'),
      api.GET('/api/settings/versions'),
      api.GET('/api/units'),
      api.GET('/api/synonyms'),
      api.GET('/api/price-indexes'),
    ])
    settings.value = checked(s)
    versions.value = checked(v)
    units.value = checked(u)
    synonyms.value = checked(syn)
    indexes.value = checked(idx)
  }

  async function saveRules(rules: Record<string, unknown>, comment: string | null): Promise<void> {
    settings.value = checked(await api.PUT('/api/settings', { body: { rules, comment } }))
    versions.value = checked(await api.GET('/api/settings/versions'))
  }

  async function saveUnit(unit: Unit): Promise<void> {
    checked(await api.PUT('/api/units/{code}', { params: { path: { code: unit.code } }, body: unit }))
    units.value = checked(await api.GET('/api/units'))
  }

  async function deleteUnit(code: string): Promise<void> {
    checked(await api.DELETE('/api/units/{code}', { params: { path: { code } } }))
    units.value = checked(await api.GET('/api/units'))
  }

  async function saveSynonym(words: string[], id?: number): Promise<void> {
    checked(
      id
        ? await api.PUT('/api/synonyms/{group_id}', { params: { path: { group_id: id } }, body: { words } })
        : await api.POST('/api/synonyms', { body: { words } }),
    )
    synonyms.value = checked(await api.GET('/api/synonyms'))
  }

  async function deleteSynonym(id: number): Promise<void> {
    checked(await api.DELETE('/api/synonyms/{group_id}', { params: { path: { group_id: id } } }))
    synonyms.value = checked(await api.GET('/api/synonyms'))
  }

  async function saveIndexes(items: PriceIndex[]): Promise<void> {
    indexes.value = checked(await api.PUT('/api/price-indexes', { body: items }))
  }

  return {
    settings,
    versions,
    units,
    synonyms,
    indexes,
    load,
    saveRules,
    saveUnit,
    deleteUnit,
    saveSynonym,
    deleteSynonym,
    saveIndexes,
  }
})
