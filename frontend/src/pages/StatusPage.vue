<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'

import { useHealthStore } from '@/stores/health'

const health = useHealthStore()

const NAMES: Record<string, { title: string; icon: string }> = {
  database: { title: 'База данных (PostgreSQL)', icon: 'mdi-database' },
  redis: { title: 'Очередь и кэш (Redis)', icon: 'mdi-tray-full' },
  worker: { title: 'Воркер пайплайна', icon: 'mdi-cogs' },
  storage: { title: 'Хранилище (Garage S3)', icon: 'mdi-archive' },
  llm: { title: 'LLM (Ollama)', icon: 'mdi-brain' },
  search: { title: 'Веб-поиск (SearXNG)', icon: 'mdi-web' },
}

const STATUS: Record<string, { label: string; color: string; icon: string }> = {
  ok: { label: 'Готов', color: 'success', icon: 'mdi-check-circle' },
  degraded: { label: 'Частично', color: 'warning', icon: 'mdi-alert' },
  down: { label: 'Недоступен', color: 'error', icon: 'mdi-close-circle' },
}

let timer: ReturnType<typeof setInterval> | undefined

onMounted(() => {
  health.refresh()
  timer = setInterval(health.refresh, 10_000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <section class="page">
    <div class="d-flex align-center justify-space-between mb-2">
      <h1 class="page-title">Состояние сервисов</h1>
      <v-btn
        prepend-icon="mdi-refresh"
        variant="tonal"
        class="text-none"
        :loading="health.loading"
        @click="health.refresh"
      >
        Обновить
      </v-btn>
    </div>

    <v-alert v-if="health.error" type="error" class="mb-4">{{ health.error }}</v-alert>

    <template v-if="health.report">
      <div class="d-flex align-center ga-3 mb-4">
        <span>Общий статус:</span>
        <v-chip
          :color="STATUS[health.report.status]?.color"
          :prepend-icon="STATUS[health.report.status]?.icon"
          label
        >
          {{ STATUS[health.report.status]?.label }}
        </v-chip>
        <span class="text-medium-emphasis text-body-2">
          версия {{ health.report.version }} · проверено
          {{ health.checkedAt?.toLocaleTimeString('ru-RU') }}
        </span>
      </div>

      <v-row>
        <v-col v-for="c in health.report.components" :key="c.name" cols="12" sm="6" lg="4">
          <v-card border flat height="100%" :data-component="c.name">
            <v-card-item>
              <template #prepend>
                <v-icon :icon="NAMES[c.name]?.icon ?? 'mdi-server'" color="secondary" />
              </template>
              <v-card-title class="text-body-1 font-weight-medium text-wrap">
                {{ NAMES[c.name]?.title ?? c.name }}
              </v-card-title>
              <template #append>
                <v-chip :color="STATUS[c.status]?.color" size="small" label>
                  {{ STATUS[c.status]?.label }}
                </v-chip>
              </template>
            </v-card-item>
            <v-card-text>
              <div class="text-break">{{ c.detail ?? '—' }}</div>
              <div class="text-medium-emphasis text-caption mt-1">{{ c.latency_ms }} мс</div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </template>

    <div v-else-if="health.loading" class="d-flex justify-center pa-8">
      <v-progress-circular indeterminate color="primary" />
    </div>
  </section>
</template>
