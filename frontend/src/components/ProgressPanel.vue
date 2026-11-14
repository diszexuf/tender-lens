<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'

import type { ProgressEvent } from '@/api/types'
import { SOURCE_STATUS, tone } from '@/utils/format'

const props = defineProps<{ events: ProgressEvent[]; status: string; autoRun: boolean }>()

const STAGES = [
  { key: 'spec', title: 'Разбор описания закупки', hint: 'LLM выделяет позиции, количество и характеристики — обычно 5–30 с' },
  { key: 'collect', title: 'Сбор предложений в источниках', hint: 'Поиск в источниках и загрузка карточек — до нескольких минут' },
  { key: 'evaluate', title: 'Сопоставление, нормализация и расчёт', hint: 'Извлечение характеристик предложений и расчёт цены' },
]

// Без автозапуска после разбора откроется проверка спецификации — следующие этапы не показываем
const reviewFirst = computed(() => !props.autoRun && ['draft', 'extracting'].includes(props.status))
const stages = computed(() => (reviewFirst.value ? STAGES.slice(0, 1) : STAGES))

const stageState = computed(() => {
  const state: Record<string, string> = {}
  for (const ev of props.events) {
    if (ev.event === 'stage' && ev.stage && ev.status) state[ev.stage] = ev.status
  }
  // Статус расчёта известен раньше первого события (задача ещё в очереди воркера)
  const current = { draft: 'spec', extracting: 'spec', collecting: 'collect', evaluating: 'evaluate' }[props.status]
  if (current && !state[current]) state[current] = props.status === 'draft' ? 'queued' : 'started'
  return state
})

const sources = computed(() => {
  const rows = new Map<string, ProgressEvent>()
  for (const ev of props.events) {
    if (ev.event === 'source' && ev.source) rows.set(`${ev.position}:${ev.source}`, ev)
  }
  return [...rows.values()].sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
})

const llm = computed(() => [...props.events].reverse().find((e) => e.event === 'llm'))

// Секундомер текущего этапа — видно, что работа идёт
const now = ref(Date.now())
const timer = setInterval(() => (now.value = Date.now()), 1000)
onUnmounted(() => clearInterval(timer))
const startedAt = Date.now()

const elapsed = computed(() => {
  const last = [...props.events].reverse().find((e) => e.event === 'stage' && e.status === 'started')
  const from = last ? Math.min(new Date(last.at).getTime(), now.value) : startedAt
  const sec = Math.max(0, Math.round((now.value - from) / 1000))
  return sec < 60 ? `${sec} с` : `${Math.floor(sec / 60)} мин ${sec % 60} с`
})

function stageIcon(key: string): { icon: string; color: string } {
  const s = stageState.value[key]
  if (s === 'done') return { icon: 'mdi-check-circle', color: 'success' }
  if (s === 'failed') return { icon: 'mdi-alert-circle', color: 'error' }
  if (s === 'started') return { icon: 'mdi-progress-clock', color: 'info' }
  if (s === 'queued') return { icon: 'mdi-timer-sand', color: 'info' }
  return { icon: 'mdi-circle-outline', color: 'grey' }
}

function stageSubtitle(key: string, hint: string): string | undefined {
  const s = stageState.value[key]
  if (s === 'queued') return 'Задача в очереди, ожидает воркер'
  if (s === 'started') return `${hint} · идёт ${elapsed.value}`
  return undefined
}
</script>

<template>
  <v-card border flat>
    <v-progress-linear indeterminate color="primary" />
    <v-card-text>
      <v-list density="compact" class="py-0">
        <v-list-item
          v-for="s in stages"
          :key="s.key"
          :title="s.title"
          :subtitle="stageSubtitle(s.key, s.hint)"
          :class="{ 'font-weight-medium': ['started', 'queued'].includes(stageState[s.key] ?? '') }"
        >
          <template #prepend>
            <v-icon :icon="stageIcon(s.key).icon" :color="stageIcon(s.key).color" />
          </template>
        </v-list-item>
      </v-list>

      <div v-if="reviewFirst" class="text-body-2 text-medium-emphasis ml-4 mt-1">
        После разбора здесь откроется спецификация для проверки — поиск начнётся после подтверждения.
      </div>

      <div v-if="llm && stageState.evaluate === 'started'" class="text-body-2 text-medium-emphasis ml-4 mb-2">
        Извлечение характеристик: позиция {{ llm.position }}, пакет {{ llm.batch }} из {{ llm.batches }}
      </div>

      <v-table v-if="sources.length" density="compact" class="mt-2">
        <thead>
          <tr>
            <th>Позиция</th>
            <th>Источник</th>
            <th>Статус</th>
            <th class="text-right">Предложений</th>
            <th class="text-right">Время</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sources" :key="`${s.position}:${s.source}`">
            <td>{{ s.position }}</td>
            <td>{{ s.source }}</td>
            <td>
              <v-chip
                size="x-small"
                label
                :color="tone(SOURCE_STATUS, s.status).color"
                :prepend-icon="tone(SOURCE_STATUS, s.status).icon"
              >
                {{ tone(SOURCE_STATUS, s.status).label }}
              </v-chip>
            </td>
            <td class="text-right">{{ s.offers ?? '—' }}</td>
            <td class="text-right">{{ s.duration_ms ? `${(s.duration_ms / 1000).toFixed(1)} с` : '—' }}</td>
          </tr>
        </tbody>
      </v-table>
    </v-card-text>
  </v-card>
</template>
