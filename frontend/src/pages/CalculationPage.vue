<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import type { OfferReport, PositionSpec, VersionComparison } from '@/api/types'
import PositionCard from '@/components/PositionCard.vue'
import ProgressPanel from '@/components/ProgressPanel.vue'
import SpecEditor from '@/components/SpecEditor.vue'
import { useCalculationStore } from '@/stores/calculation'
import {
  CALC_STATUS,
  CANCELLABLE_STATUSES,
  OUTCOME,
  POSITION_STATUS,
  VERSION_TRIGGER,
  dateTime,
  money,
  num,
  tone,
} from '@/utils/format'

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

// Правка спецификации после расчёта (UC-05)
const completed = computed(() => detail.value?.status === 'completed')
const editingSpec = ref(false)
async function saveAfterRun(positions: PositionSpec[]): Promise<void> {
  if (await store.saveSpecAfterRun(id.value, positions)) editingSpec.value = false
}
const latestVersion = computed(() => detail.value?.versions.at(-1)?.version_no ?? null)

// Выгрузки отчёта
function exportUrl(format: 'xlsx' | 'pdf' | 'html'): string {
  const v = version.value ? '&version=' + version.value.version_no : ''
  return '/api/calculations/' + id.value + '/export?format=' + format + v
}
const auditUrl = computed(
  () => '/api/calculations/' + id.value + '/audit' + (version.value ? '?version=' + version.value.version_no : ''),
)

// Ручные решения (FR-06.9)
const decision = ref<{ offer: OfferReport; decision: 'included' | 'excluded' } | null>(null)
const comment = ref('')
async function decide(offer: OfferReport, value: 'included' | 'excluded' | null): Promise<void> {
  if (value === null) {
    await store.setDecision(id.value, offer.id, null)
    return
  }
  comment.value = offer.manual_comment ?? ''
  decision.value = { offer, decision: value }
}
async function confirmDecision(): Promise<void> {
  if (!decision.value) return
  const { offer, decision: value } = decision.value
  if (await store.setDecision(id.value, offer.id, value, comment.value.trim())) decision.value = null
}

// Сравнение версий (UC-08)
const comparing = ref(false)
const versionRows = ref<VersionComparison[]>([])
async function compare(): Promise<void> {
  versionRows.value = await store.loadVersions(id.value)
  comparing.value = true
}
const comparePositions = computed(() => {
  const names = new Map<number, string>()
  for (const v of versionRows.value) for (const p of v.positions) names.set(p.ordinal, p.name)
  return [...names.entries()].sort((a, b) => a[0] - b[0])
})
function cell(v: VersionComparison, ordinal: number) {
  return v.positions.find((p) => p.ordinal === ordinal)
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
        <v-menu v-if="report && version">
          <template #activator="{ props: menu }">
            <v-btn v-bind="menu" variant="tonal" prepend-icon="mdi-download" class="text-none">Выгрузить</v-btn>
          </template>
          <v-list density="compact">
            <v-list-item :href="exportUrl('xlsx')" prepend-icon="mdi-file-excel" title="XLSX с формулами" />
            <v-list-item :href="exportUrl('pdf')" prepend-icon="mdi-file-pdf-box" title="PDF" />
            <v-list-item :href="exportUrl('html')" target="_blank" prepend-icon="mdi-printer" title="Для печати (HTML)" />
            <v-list-item :href="auditUrl" target="_blank" prepend-icon="mdi-code-json" title="Полный след (JSON)" />
          </v-list>
        </v-menu>
        <v-btn
          v-if="completed"
          variant="tonal"
          prepend-icon="mdi-pencil"
          class="text-none"
          @click="editingSpec = !editingSpec"
        >
          {{ editingSpec ? 'Скрыть спецификацию' : 'Изменить спецификацию' }}
        </v-btn>
        <v-btn
          v-if="completed"
          variant="tonal"
          prepend-icon="mdi-refresh"
          class="text-none"
          @click="store.action(id, 'recalculate')"
        >
          Пересчитать
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

      <template v-if="completed && editingSpec">
        <h2 class="text-h6 mb-2">Изменение спецификации</h2>
        <p class="text-body-2 text-medium-emphasis mb-4">
          Правка характеристик и количества — перерасчёт на собранных данных. Новые позиции и позиции с
          изменённым наименованием или поисковым запросом собираются заново.
        </p>
        <SpecEditor
          :positions="detail.spec"
          :warnings="[]"
          :busy="store.busy"
          submit-label="Сохранить и пересчитать"
          @run="saveAfterRun"
        />
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
            <v-btn
              v-if="versionItems.length > 1"
              variant="text"
              size="small"
              prepend-icon="mdi-compare-horizontal"
              class="text-none"
              @click="compare"
            >
              Сравнить версии
            </v-btn>
            <div class="text-caption text-medium-emphasis">
              {{ VERSION_TRIGGER[version.trigger] ?? version.trigger }} · модель {{ version.llm_model }} ·
              настройки v{{ version.settings_version_id }}
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
          <PositionCard
            v-for="p in report.positions"
            :key="p.id"
            :position="p"
            :reasons="report.reasons"
            :editable="completed && version.version_no === latestVersion"
            @decide="decide"
          />
        </v-expansion-panels>
      </template>
    <v-dialog :model-value="!!decision" max-width="560" @update:model-value="decision = null">
      <v-card v-if="decision">
        <v-card-title>{{ decision.decision === 'included' ? 'Включить в выборку' : 'Исключить из выборки' }}</v-card-title>
        <v-card-text>
          <div class="text-body-2 mb-3">{{ decision.offer.title }}</div>
          <v-textarea v-model="comment" label="Обоснование (обязательно)" rows="3" auto-grow counter="2000" />
          <div class="text-caption text-medium-emphasis">
            Будет создана новая версия расчёта; решение сохранится во всех следующих версиях.
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn class="text-none" @click="decision = null">Отмена</v-btn>
          <v-btn
            color="primary"
            variant="flat"
            class="text-none"
            :disabled="comment.trim().length < 3"
            @click="confirmDecision"
          >
            Сохранить и пересчитать
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="comparing" max-width="1100">
      <v-card>
        <v-card-title>Сравнение версий</v-card-title>
        <v-card-text>
          <v-table density="compact">
            <thead>
              <tr>
                <th>Позиция</th>
                <th v-for="v in versionRows" :key="v.id" class="text-right">
                  v{{ v.version_no }}
                  <div class="text-caption text-medium-emphasis">
                    {{ VERSION_TRIGGER[v.trigger] ?? v.trigger }} · настройки v{{ v.settings_version_id }}
                    <span v-if="v.manual_decisions"> · ручных {{ v.manual_decisions }}</span>
                  </div>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="[ordinal, name] in comparePositions" :key="ordinal">
                <td>{{ ordinal }}. {{ name }}</td>
                <td v-for="v in versionRows" :key="v.id" class="text-right">
                  <template v-if="cell(v, ordinal)">
                    <div :class="'text-' + tone(POSITION_STATUS, cell(v, ordinal)?.status).color">
                      {{ money(cell(v, ordinal)?.unit_price) }}
                    </div>
                    <div class="text-caption text-medium-emphasis">
                      n {{ cell(v, ordinal)?.n ?? '—' }} · CV {{ num(cell(v, ordinal)?.cv) }} %
                    </div>
                  </template>
                  <span v-else>—</span>
                </td>
              </tr>
              <tr class="font-weight-bold">
                <td>НМЦК</td>
                <td v-for="v in versionRows" :key="v.id" class="text-right">{{ money(v.nmck_total) }}</td>
              </tr>
            </tbody>
          </v-table>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn class="text-none" @click="comparing = false">Закрыть</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
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
