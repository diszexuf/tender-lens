<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import type { PositionSpec } from '@/api/types'
import PositionCard from '@/components/PositionCard.vue'
import ProgressPanel from '@/components/ProgressPanel.vue'
import SpecEditor from '@/components/SpecEditor.vue'
import { useCalculationStore } from '@/stores/calculation'
import { CALC_STATUS, CANCELLABLE_STATUSES, OUTCOME, dateTime, money, tone } from '@/utils/format'

const route = useRoute()
const store = useCalculationStore()
const id = computed(() => String(route.params.id))

watch(
  id,
  async (value) => {
    store.reset()
    await store.load(value)
  },
  { immediate: true },
)
onUnmounted(() => store.reset())

const detail = computed(() => store.detail)
const report = computed(() => store.report)
const status = computed(() => tone(CALC_STATUS, detail.value?.status))
const version = computed(() => report.value?.version ?? null)
const editable = computed(() => detail.value?.status === 'spec_ready')
const versionItems = computed(() =>
  (detail.value?.versions ?? [])
    .map((v) => ({ title: `Версия ${v.version_no} · ${dateTime(v.created_at)}`, value: v.version_no }))
    .reverse(),
)

// Позиции отчёта: одна — раскрыта, несколько — свёрнуты до сводки
const openPositions = ref<string[]>([])
watch(
  () => report.value?.positions.map((p) => p.id).join(','),
  () => {
    const ids = report.value?.positions.map((p) => p.id) ?? []
    openPositions.value = ids.length === 1 ? ids : []
  },
)
const allOpen = computed(() => !!report.value && openPositions.value.length === report.value.positions.length)
function toggleAll(): void {
  openPositions.value = allOpen.value ? [] : (report.value?.positions.map((p) => p.id) ?? [])
}

// Разбор закончился на этой странице — показываем спецификацию
const specAnchor = ref<HTMLElement | null>(null)
watch(
  () => detail.value?.status,
  async (now, before) => {
    if (now === 'spec_ready' && before && ['draft', 'extracting'].includes(before)) {
      await nextTick()
      specAnchor.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  },
)

async function run(positions: PositionSpec[]): Promise<void> {
  await store.saveSpecAndRun(id.value, positions)
}
</script>

<template>
  <section class="page">
    <v-alert v-if="store.error" type="error" class="mb-4" closable>{{ store.error }}</v-alert>

    <template v-if="detail">
      <div class="d-flex align-start ga-4 mb-4">
        <div class="flex-grow-1">
          <h1 class="page-title mb-1">Расчёт НМЦК</h1>
          <div class="text-body-2 text-medium-emphasis">
            {{ dateTime(detail.created_at) }} · {{ detail.spec.length }} поз.
          </div>
        </div>
        <v-chip :color="status.color" :prepend-icon="status.icon" label size="large">{{ status.label }}</v-chip>
        <v-btn
          v-if="detail.status === 'completed'"
          variant="tonal"
          prepend-icon="mdi-refresh"
          class="text-none"
          @click="store.action(id, 'recalculate')"
        >
          Перерасчитать
        </v-btn>
        <v-btn
          v-if="['failed', 'interrupted'].includes(detail.status) && detail.spec.length"
          variant="tonal"
          prepend-icon="mdi-play"
          class="text-none"
          @click="store.action(id, 'run')"
        >
          Запустить снова
        </v-btn>
        <v-btn
          v-if="CANCELLABLE_STATUSES.includes(detail.status)"
          variant="text"
          color="error"
          prepend-icon="mdi-stop"
          class="text-none"
          @click="store.action(id, 'cancel')"
        >
          Отменить
        </v-btn>
      </div>

      <v-card border flat class="mb-4">
        <v-card-text class="text-body-2 input-text">{{ detail.input_text }}</v-card-text>
      </v-card>

      <v-alert v-if="detail.error" type="error" class="mb-4">{{ detail.error }}</v-alert>

      <ProgressPanel
        v-if="store.isActive"
        :events="store.events"
        :status="detail.status"
        :auto-run="detail.auto_run"
        class="mb-4"
      />

      <template v-if="editable">
        <div ref="specAnchor" class="scroll-anchor" />
        <h2 class="text-h6 mb-2">Проверьте спецификацию</h2>
        <p class="text-body-2 text-medium-emphasis mb-4">
          Характеристики извлечены автоматически. Обязательные характеристики исключают несовпадающие
          предложения, желательные — влияют на релевантность.
        </p>
        <SpecEditor :positions="detail.spec" :warnings="detail.spec_warnings" :busy="store.busy" @run="run" />
      </template>

      <template v-if="report && version">
        <v-card border flat class="mb-4" color="primary" variant="tonal">
          <v-card-text class="d-flex flex-wrap align-center ga-6">
            <div>
              <div class="text-caption">НМЦК</div>
              <div class="text-h4 font-weight-bold">{{ money(version.nmck_total) }}</div>
            </div>
            <v-chip
              :color="tone(OUTCOME, version.outcome).color"
              :prepend-icon="tone(OUTCOME, version.outcome).icon"
              label
            >
              {{ tone(OUTCOME, version.outcome).label }}
            </v-chip>
            <v-spacer />
            <v-select
              v-if="versionItems.length > 1"
              :model-value="version.version_no"
              :items="versionItems"
              density="compact"
              variant="outlined"
              hide-details
              style="max-width: 280px"
              @update:model-value="(v: number) => store.loadReport(id, v)"
            />
            <div class="text-caption text-medium-emphasis">
              Модель {{ version.llm_model }} · настройки v{{ version.settings_version_id }}
            </div>
          </v-card-text>
        </v-card>

        <div class="d-flex align-center mb-2">
          <h2 class="text-h6">Позиции ({{ report.positions.length }})</h2>
          <v-spacer />
          <v-btn
            v-if="report.positions.length > 1"
            variant="text"
            size="small"
            class="text-none"
            :prepend-icon="allOpen ? 'mdi-unfold-less-horizontal' : 'mdi-unfold-more-horizontal'"
            @click="toggleAll"
          >
            {{ allOpen ? 'Свернуть все' : 'Развернуть все' }}
          </v-btn>
        </div>
        <v-expansion-panels v-model="openPositions" multiple variant="accordion" class="mb-4">
          <PositionCard v-for="p in report.positions" :key="p.id" :position="p" :reasons="report.reasons" />
        </v-expansion-panels>
      </template>
    </template>

    <div v-else-if="!store.error" class="d-flex justify-center pa-8">
      <v-progress-circular indeterminate color="primary" />
    </div>
  </section>
</template>

<style scoped>
.input-text {
  white-space: pre-line;
}
.scroll-anchor {
  scroll-margin-top: 80px;
}
</style>
