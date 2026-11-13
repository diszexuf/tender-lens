import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, type Schemas } from '@/api/client'

export type HealthReport = Schemas['HealthReport']

export const useHealthStore = defineStore('health', () => {
  const report = ref<HealthReport | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const checkedAt = ref<Date | null>(null)

  async function refresh(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      // При 503 отчёт тоже приходит — openapi-fetch кладёт его в поле error
      const { data, error: body, response } = await api.GET('/api/health')
      const payload = (data ?? body) as HealthReport | undefined
      if (payload && 'components' in payload) {
        report.value = payload
      } else {
        error.value = `API ответил с кодом ${response.status}`
      }
    } catch {
      error.value = 'API недоступен'
    } finally {
      loading.value = false
      checkedAt.value = new Date()
    }
  }

  return { report, loading, error, checkedAt, refresh }
})
