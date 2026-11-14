<script setup lang="ts">
import { computed, ref } from 'vue'

import type { OfferReport, PositionReport, SpecAttribute } from '@/api/types'
import OfferTable from '@/components/OfferTable.vue'
import { FLAGS, POSITION_STATUS, SOURCE_STATUS, money, num, tone } from '@/utils/format'

const props = defineProps<{ position: PositionReport; reasons: Record<string, string>; editable?: boolean }>()
const emit = defineEmits<{ decide: [offer: OfferReport, decision: 'included' | 'excluded' | null] }>()

const included = computed(() => props.position.offers.filter((o) => o.decision === 'included'))
const excluded = computed(() => props.position.offers.filter((o) => o.decision !== 'included'))
const excludedByReason = computed(() => {
  const counts: Record<string, number> = {}
  for (const o of excluded.value) counts[o.reason_code ?? 'other'] = (counts[o.reason_code ?? 'other'] ?? 0) + 1
  return Object.entries(counts).sort((a, b) => b[1] - a[1])
})
const status = computed(() => tone(POSITION_STATUS, props.position.result?.status))
const stats = computed(() => props.position.result?.stats ?? null)

// Вкладки вместо длинной ленты; если в выборке пусто — сразу показываем исключённые
const tab = ref(included.value.length || !excluded.value.length ? 'included' : 'excluded')
const reasonFilter = ref<string | null>(null)
const excludedShown = computed(() =>
  reasonFilter.value
    ? excluded.value.filter((o) => (o.reason_code ?? 'other') === reasonFilter.value)
    : excluded.value,
)

function attrText(a: SpecAttribute): string {
  const unit = a.unit ? ` ${a.unit}` : ''
  switch (a.type) {
    case 'number':
      return `${a.name} ${{ gte: '≥', lte: '≤' }[a.operator as string] ?? '='} ${a.value_number}${unit}`
    case 'range':
      return `${a.name} ${a.value_min ?? '…'}–${a.value_max ?? '…'}${unit}`
    case 'enum':
      return `${a.name}: ${(a.values ?? []).join(' / ')}`
    case 'bool':
      return `${a.name}: ${a.value_bool ? 'да' : 'нет'}`
    default:
      return `${a.name}: ${a.value_text}`
  }
}
</script>

<template>
  <v-expansion-panel :value="position.id">
    <v-expansion-panel-title>
      <div class="d-flex flex-wrap align-center ga-3 w-100 pr-2">
        <div class="flex-grow-1 position-name">
          <div class="text-body-1 font-weight-medium">
            {{ position.spec.ordinal }}. {{ position.spec.name }} — {{ num(position.spec.quantity) }}
            {{ position.spec.unit_raw }}
          </div>
          <div class="text-caption text-medium-emphasis">
            в выборке {{ included.length }} из {{ position.offers.length }}
            <span v-if="stats?.cv"> · CV {{ num(stats.cv as string) }} %</span>
          </div>
        </div>
        <div class="text-right">
          <div class="text-body-1 font-weight-bold">{{ money(position.result?.total_price) }}</div>
          <div v-if="position.result?.unit_price" class="text-caption text-medium-emphasis">
            {{ money(position.result.unit_price) }} за ед.
          </div>
        </div>
        <v-chip :color="status.color" :prepend-icon="status.icon" label size="small">{{ status.label }}</v-chip>
      </div>
    </v-expansion-panel-title>

    <v-expansion-panel-text v-if="position.result">
      <div class="text-body-2 mb-3">
        <span v-for="a in position.spec.attributes" :key="a.name" class="mr-3">
          {{ attrText(a) }}<span v-if="!a.required" class="text-medium-emphasis"> (желат.)</span>
        </span>
      </div>

      <v-table v-if="stats" density="compact" class="mb-2">
        <tbody>
          <tr>
            <td>Предложений в выборке</td>
            <td class="text-right">{{ stats.n }}</td>
            <td>Среднее</td>
            <td class="text-right">{{ money(stats.mean as string) }}</td>
            <td>Медиана</td>
            <td class="text-right">{{ money(stats.median as string) }}</td>
          </tr>
          <tr>
            <td>Минимум / максимум</td>
            <td class="text-right">{{ money(stats.min as string) }} / {{ money(stats.max as string) }}</td>
            <td>Ст. отклонение σ</td>
            <td class="text-right">{{ money(stats.stdev as string) }}</td>
            <td>Коэффициент вариации</td>
            <td class="text-right font-weight-bold">{{ stats.cv ? `${num(stats.cv as string)} %` : '—' }}</td>
          </tr>
        </tbody>
      </v-table>

      <v-alert v-for="h in position.result.hints" :key="h" type="warning" density="compact" class="mb-2">
        {{ h }}
      </v-alert>

      <v-tabs v-model="tab" density="compact" color="primary" class="mt-2">
        <v-tab value="included" class="text-none">Включены ({{ included.length }})</v-tab>
        <v-tab value="excluded" class="text-none">Исключены ({{ excluded.length }})</v-tab>
        <v-tab value="log" class="text-none">Ход расчёта</v-tab>
        <v-tab value="sources" class="text-none">Источники ({{ position.sources.length }})</v-tab>
      </v-tabs>
      <v-divider />

      <v-tabs-window v-model="tab" class="pt-3">
        <v-tabs-window-item value="included">
          <OfferTable
            v-if="included.length"
            :offers="included"
            included
            :editable="editable"
            @decide="(o, d) => emit('decide', o, d)"
          />
          <div v-else class="text-body-2 text-medium-emphasis">Нет предложений в выборке</div>
        </v-tabs-window-item>

        <v-tabs-window-item value="excluded">
          <v-chip-group v-model="reasonFilter" column class="mb-1" selected-class="text-primary">
            <v-chip v-for="[code, n] in excludedByReason" :key="code" :value="code" size="small" label filter>
              {{ reasons[code] ?? code }}: {{ n }}
            </v-chip>
          </v-chip-group>
          <OfferTable
            v-if="excludedShown.length"
            :offers="excludedShown"
            :included="false"
            :editable="editable"
            @decide="(o, d) => emit('decide', o, d)"
          />
          <div v-else class="text-body-2 text-medium-emphasis">Исключённых предложений нет</div>
        </v-tabs-window-item>

        <v-tabs-window-item value="log">
          <ol class="text-body-2 pl-4">
            <li v-for="(line, i) in position.result.filter_log" :key="i">{{ line }}</li>
          </ol>
          <div v-if="position.result.flags.length" class="mt-2">
            <v-chip v-for="f in position.result.flags" :key="f" size="x-small" label class="mr-1">
              {{ FLAGS[f] ?? f }}
            </v-chip>
          </div>
        </v-tabs-window-item>

        <v-tabs-window-item value="sources">
          <v-table density="compact">
            <tbody>
              <tr v-for="s in position.sources" :key="s.source_name">
                <td>{{ s.source_name }}</td>
                <td>
                  <v-chip size="x-small" label :color="tone(SOURCE_STATUS, s.status).color">
                    {{ tone(SOURCE_STATUS, s.status).label }}
                  </v-chip>
                </td>
                <td class="text-right">{{ s.offers_found }} предл.</td>
                <td class="text-right">{{ (s.duration_ms / 1000).toFixed(1) }} с</td>
                <td class="text-caption text-medium-emphasis">{{ s.error }}</td>
              </tr>
            </tbody>
          </v-table>
        </v-tabs-window-item>
      </v-tabs-window>
    </v-expansion-panel-text>
  </v-expansion-panel>
</template>

<style scoped>
.position-name {
  min-width: 240px;
}
</style>
