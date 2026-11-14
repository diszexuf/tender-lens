<script setup lang="ts">
import type { OfferReport } from '@/api/types'
import { EXTRACTION, FLAGS, MATCH_STATUS, VAT_STATUS, date, money, num, tone } from '@/utils/format'

defineProps<{ offers: OfferReport[]; included: boolean; editable?: boolean }>()
const emit = defineEmits<{ decide: [offer: OfferReport, decision: 'included' | 'excluded' | null] }>()

const headers = [
  { title: 'Предложение', key: 'title', sortable: false },
  { title: 'Источник', key: 'source_name', width: 160 },
  { title: 'Цена', key: 'price', align: 'end' as const, width: 120 },
  { title: 'За ед.', key: 'normalized_price', align: 'end' as const, width: 120 },
  { title: 'Релев.', key: 'relevance', align: 'end' as const, width: 80 },
  { title: '', key: 'data-table-expand', width: 40 },
]
/** Кто нашёл значение характеристики — чтобы ошибку извлечения было видно в отчёте */
const VALUE_SOURCE: Record<string, string> = {
  title: 'Из названия',
  code: 'Найдено в тексте',
  llm: 'Определено моделью',
}
</script>

<template>
  <v-data-table
    :headers="headers"
    :items="offers"
    item-value="id"
    density="compact"
    show-expand
    expand-on-click
    :items-per-page="10"
    :items-per-page-options="[10, 25, 50, { value: -1, title: 'все' }]"
    :hide-default-footer="offers.length <= 10"
    class="offer-table"
  >
    <template #[`item.title`]="{ item }">
      <div class="py-1">
        <a :href="item.url" target="_blank" rel="noopener noreferrer" class="offer-link" @click.stop>{{ item.title }}</a>
        <div class="text-caption text-medium-emphasis">
          {{ item.supplier_name || '—' }}<span v-if="item.supplier_inn"> · ИНН {{ item.supplier_inn }}</span>
          · {{ date(item.price_date) }}
        </div>
        <div v-if="!included" class="text-caption text-error">{{ item.reason_text }}</div>
        <div v-if="item.manual_decision" class="text-caption text-primary">
          <v-icon icon="mdi-account-check" size="x-small" /> Ручное решение: {{ item.manual_comment }}
        </div>
      </div>
    </template>
    <template #[`item.price`]="{ item }">
      {{ money(item.price) }}
      <div v-if="item.unit_raw" class="text-caption text-medium-emphasis">за {{ item.unit_raw }}</div>
      <div class="text-caption text-medium-emphasis">{{ VAT_STATUS[item.vat_status] ?? item.vat_status }}</div>
    </template>
    <template #[`item.normalized_price`]="{ item }">
      <strong v-if="included">{{ money(item.normalized_price) }}</strong>
      <span v-else>{{ money(item.normalized_price) }}</span>
    </template>
    <template #[`item.relevance`]="{ item }">{{ num(item.relevance) }}</template>

    <template #expanded-row="{ columns, item }">
      <tr>
        <td :colspan="columns.length" class="bg-grey-lighten-5 py-3">
          <v-row dense>
            <v-col cols="12" md="7">
              <div class="text-subtitle-2 mb-1">Сравнение характеристик</div>
              <div v-if="!item.attribute_matches.length" class="text-body-2 text-medium-emphasis">
                Характеристики не сравнивались
              </div>
              <div v-for="m in item.attribute_matches" :key="String(m.name)" class="d-flex ga-2 align-start mb-1">
                <v-icon
                  :icon="tone(MATCH_STATUS, String(m.status)).icon"
                  :color="tone(MATCH_STATUS, String(m.status)).color"
                  size="small"
                />
                <div class="text-body-2">
                  <strong>{{ m.requirement }}</strong>
                  <span v-if="m.required" class="text-caption text-medium-emphasis"> (обяз.)</span>
                  — {{ m.explanation }}
                  <div v-if="m.source" class="text-caption text-medium-emphasis">
                    {{ VALUE_SOURCE[String(m.source)] ?? m.source }}<span v-if="m.evidence">: «{{ m.evidence }}»</span>
                  </div>
                </div>
              </div>
            </v-col>
            <v-col cols="12" md="5">
              <div class="text-subtitle-2 mb-1">Приведение цены</div>
              <div v-if="!item.normalization_steps.length" class="text-body-2 text-medium-emphasis">
                Без пересчёта
              </div>
              <div v-for="(s, i) in item.normalization_steps" :key="i" class="text-body-2">
                {{ money(String(s.before)) }} → {{ money(String(s.after)) }}: {{ s.reason }}
              </div>
              <div class="text-subtitle-2 mt-3 mb-1">Происхождение</div>
              <div class="text-body-2">
                Извлечено: {{ EXTRACTION[item.extraction_method] ?? item.extraction_method }} ·
                получено {{ date(item.fetched_at) }}
              </div>
              <div v-if="item.snapshot_ids.length" class="text-body-2">
                Страница на момент сбора:
                <a
                  v-for="(sid, i) in item.snapshot_ids"
                  :key="sid"
                  :href="`/api/snapshots/${sid}`"
                  target="_blank"
                  rel="noopener"
                  class="mr-2"
                >{{ item.snapshot_ids.length > 1 ? 'копия ' + (i + 1) : 'открыть' }}</a>
              </div>
              <div v-if="item.region" class="text-body-2">Регион: {{ item.region }}</div>
              <div v-if="item.flags.length" class="mt-2 d-flex flex-wrap ga-1">
                <v-chip v-for="f in item.flags" :key="f" size="x-small" label>{{ FLAGS[f] ?? f }}</v-chip>
              </div>
              <div v-if="editable" class="mt-3 d-flex flex-wrap ga-2">
                <v-btn
                  v-if="item.decision === 'included'"
                  size="small"
                  variant="tonal"
                  color="error"
                  prepend-icon="mdi-minus-circle"
                  class="text-none"
                  @click="emit('decide', item, 'excluded')"
                >
                  Исключить вручную
                </v-btn>
                <v-btn
                  v-else
                  size="small"
                  variant="tonal"
                  color="success"
                  prepend-icon="mdi-plus-circle"
                  class="text-none"
                  @click="emit('decide', item, 'included')"
                >
                  Включить вручную
                </v-btn>
                <v-btn
                  v-if="item.manual_decision"
                  size="small"
                  variant="text"
                  prepend-icon="mdi-undo"
                  class="text-none"
                  @click="emit('decide', item, null)"
                >
                  Отменить ручное решение
                </v-btn>
              </div>
            </v-col>
          </v-row>
        </td>
      </tr>
    </template>
  </v-data-table>
</template>

<style scoped>
.offer-link {
  color: inherit;
  text-decoration: none;
}
.offer-link:hover {
  text-decoration: underline;
}
</style>
