<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import SchemaForm from '@/components/SchemaForm.vue'
import { useSettingsStore, type PriceIndex, type Unit } from '@/stores/settings'
import { dateTime } from '@/utils/format'

const store = useSettingsStore()
const tab = ref('rules')
const busy = ref(false)
const error = ref('')
const saved = ref('')

async function perform(action: () => Promise<void>, done = ''): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = ''
  saved.value = ''
  try {
    await action()
    saved.value = done
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Не удалось выполнить запрос'
  } finally {
    busy.value = false
  }
}

onMounted(() =>
  perform(async () => {
    await store.load()
    resetRules()
    indexDraft.value = store.indexes.map((i) => ({ ...i }))
  }),
)

// ---------------------------------------------------------------- правила
type FormSchema = InstanceType<typeof SchemaForm>["$props"]["schema"]
const AGE_KEY = 'max_price_age_days'
const rules = ref<Record<string, unknown>>({})
const ages = ref<Record<string, number>>({})
const comment = ref('')
const rulesSchema = computed<FormSchema>(() => {
  const schema = (store.settings?.rules_schema ?? {}) as FormSchema
  const properties = { ...(schema.properties ?? {}) }
  delete properties[AGE_KEY] // словарь «тип источника → дни» — отдельными полями ниже
  return { ...schema, properties }
})
function resetRules(): void {
  const current = { ...(store.settings?.rules ?? {}) }
  ages.value = { ...((current[AGE_KEY] as Record<string, number>) ?? {}) }
  delete current[AGE_KEY]
  rules.value = current
  comment.value = ''
}
async function saveRules(): Promise<void> {
  await perform(async () => {
    await store.saveRules({ ...rules.value, [AGE_KEY]: ages.value }, comment.value || null)
    resetRules()
  }, 'Сохранено: новые расчёты и перерасчёты используют эту версию настроек')
}

// ---------------------------------------------------------------- единицы
const unitHeaders = [
  { title: 'Код ОКЕИ', key: 'code', width: 100 },
  { title: 'Наименование', key: 'name' },
  { title: 'Обозн.', key: 'short', width: 90 },
  { title: 'Размерность', key: 'dimension', width: 130 },
  { title: 'Коэффициент', key: 'factor', align: 'end' as const, width: 130 },
  { title: 'Написания', key: 'aliases', sortable: false },
  { title: '', key: 'actions', sortable: false, width: 100 },
]
const unitSearch = ref('')
const unitDraft = ref<Unit | null>(null)
const unitIsNew = ref(false)
function editUnit(unit?: Unit): void {
  unitIsNew.value = !unit
  unitDraft.value = unit
    ? { ...unit, aliases: [...(unit.aliases ?? [])] }
    : { code: '', name: '', short: '', dimension: 'count', factor: '1', aliases: [] }
}
async function saveUnit(): Promise<void> {
  if (!unitDraft.value) return
  const unit = unitDraft.value
  await perform(async () => {
    await store.saveUnit(unit)
    unitDraft.value = null
  }, 'Единица сохранена')
}

// ---------------------------------------------------------------- синонимы
const synonymSearch = ref('')
const newGroup = ref<string[]>([])
const synonymsShown = computed(() => {
  const q = synonymSearch.value.trim().toLowerCase()
  return q ? store.synonyms.filter((g) => g.words.some((w) => w.includes(q))) : store.synonyms
})

// ---------------------------------------------------------------- индексы
const indexDraft = ref<PriceIndex[]>([])
function addMonth(): void {
  const last = indexDraft.value.at(-1)?.period
  const d = last ? new Date(last) : new Date()
  d.setMonth(d.getMonth() + (last ? 1 : 0), 1)
  indexDraft.value.push({ period: d.toISOString().slice(0, 10), value: '100', note: null })
}
async function saveIndexes(): Promise<void> {
  await perform(async () => {
    await store.saveIndexes(indexDraft.value)
    indexDraft.value = store.indexes.map((i) => ({ ...i }))
  }, 'Таблица индексов сохранена')
}
const indexTotal = computed(() =>
  indexDraft.value.reduce((acc, i) => acc * (Number(i.value) / 100), 1),
)
</script>

<template>
  <section class="page">
    <h1 class="page-title">Настройки</h1>
    <v-alert v-if="error" type="error" class="mb-4" closable>{{ error }}</v-alert>
    <v-alert v-if="saved" type="success" class="mb-4" closable>{{ saved }}</v-alert>

    <v-tabs v-model="tab" color="primary" class="mb-4">
      <v-tab value="rules" class="text-none">Правила расчёта</v-tab>
      <v-tab value="units" class="text-none">Единицы ОКЕИ</v-tab>
      <v-tab value="synonyms" class="text-none">Синонимы</v-tab>
      <v-tab value="indexes" class="text-none">Индексы цен</v-tab>
    </v-tabs>

    <v-tabs-window v-model="tab">
      <v-tabs-window-item value="rules">
        <v-row>
          <v-col cols="12" md="8">
            <v-card border flat>
              <v-card-text>
                <div class="text-caption text-medium-emphasis mb-3">
                  Текущая версия настроек: v{{ store.settings?.id }}. Сохранение создаёт новую версию —
                  расчёты ссылаются на версию, с которой выполнены.
                </div>
                <SchemaForm v-if="store.settings" v-model="rules" :schema="rulesSchema" />
                <fieldset class="pa-3 rounded mb-2">
                  <legend>Максимальный возраст цены, дней</legend>
                  <v-row dense>
                    <v-col v-for="(_, key) in ages" :key="key" cols="6">
                      <v-text-field
                        v-model.number="ages[key]"
                        type="number"
                        min="1"
                        :label="key === 'default' ? 'Остальные источники' : `Источники типа ${key}`"
                      />
                    </v-col>
                  </v-row>
                </fieldset>
                <v-text-field v-model="comment" label="Комментарий к версии" maxlength="500" />
              </v-card-text>
              <v-card-actions>
                <v-btn class="text-none" :disabled="busy" @click="resetRules">Отменить изменения</v-btn>
                <v-spacer />
                <v-btn color="primary" variant="flat" class="text-none" :loading="busy" @click="saveRules">
                  Сохранить новую версию
                </v-btn>
              </v-card-actions>
            </v-card>
          </v-col>
          <v-col cols="12" md="4">
            <v-card border flat>
              <v-card-title class="text-subtitle-1">История версий</v-card-title>
              <v-list density="compact">
                <v-list-item
                  v-for="v in store.versions"
                  :key="v.id"
                  :title="`v${v.id} · ${dateTime(v.created_at)}`"
                  :subtitle="v.comment ?? ''"
                />
              </v-list>
            </v-card>
          </v-col>
        </v-row>
      </v-tabs-window-item>

      <v-tabs-window-item value="units">
        <div class="d-flex ga-2 mb-2">
          <v-text-field
            v-model="unitSearch"
            prepend-inner-icon="mdi-magnify"
            label="Поиск"
            density="compact"
            hide-details
            style="max-width: 320px"
          />
          <v-spacer />
          <v-btn color="primary" prepend-icon="mdi-plus" class="text-none" @click="editUnit()">Добавить</v-btn>
        </div>
        <v-card border flat>
          <v-data-table
            :headers="unitHeaders"
            :items="store.units"
            :search="unitSearch"
            item-value="code"
            density="compact"
            :items-per-page="25"
          >
            <template #[`item.aliases`]="{ item }">
              <span class="text-caption">{{ (item.aliases ?? []).join(', ') }}</span>
            </template>
            <template #[`item.actions`]="{ item }">
              <v-btn icon="mdi-pencil" size="x-small" variant="text" @click="editUnit(item)" />
              <v-btn
                icon="mdi-delete"
                size="x-small"
                variant="text"
                color="error"
                :disabled="busy"
                @click="perform(() => store.deleteUnit(item.code), 'Единица удалена')"
              />
            </template>
          </v-data-table>
        </v-card>
        <div class="text-caption text-medium-emphasis mt-2">
          Пересчёт выполняется внутри одной размерности: цена делится на отношение коэффициентов
          (1 км = 1000 м при коэффициентах 1000 и 1).
        </div>
      </v-tabs-window-item>

      <v-tabs-window-item value="synonyms">
        <v-card border flat class="mb-3">
          <v-card-text class="d-flex ga-2 align-center">
            <v-combobox
              v-model="newGroup"
              multiple
              chips
              closable-chips
              label="Новая группа: слова через Enter, первое — каноническое"
              hide-details
            />
            <v-btn
              color="primary"
              class="text-none"
              :disabled="newGroup.length < 2 || busy"
              @click="perform(async () => { await store.saveSynonym(newGroup); newGroup = [] }, 'Группа добавлена')"
            >
              Добавить
            </v-btn>
          </v-card-text>
        </v-card>
        <v-text-field
          v-model="synonymSearch"
          prepend-inner-icon="mdi-magnify"
          label="Поиск слова"
          density="compact"
          hide-details
          class="mb-2"
          style="max-width: 320px"
        />
        <v-card v-for="g in synonymsShown" :key="g.id" border flat class="mb-2">
          <v-card-text class="d-flex ga-2 align-center py-2">
            <v-combobox v-model="g.words" multiple chips closable-chips hide-details density="compact" />
            <v-btn
              icon="mdi-content-save"
              size="small"
              variant="text"
              :disabled="busy"
              @click="perform(() => store.saveSynonym(g.words, g.id), 'Группа сохранена')"
            />
            <v-btn
              icon="mdi-delete"
              size="small"
              variant="text"
              color="error"
              :disabled="busy"
              @click="perform(() => store.deleteSynonym(g.id), 'Группа удалена')"
            />
          </v-card-text>
        </v-card>
      </v-tabs-window-item>

      <v-tabs-window-item value="indexes">
        <v-alert type="info" variant="tonal" density="compact" class="mb-3">
          ИПЦ месяца в % к предыдущему месяцу (Росстат). Цена месяца M умножается на индексы всех
          следующих месяцев таблицы. Стартовые значения — годовой ИПЦ, разнесённый поровну по месяцам:
          уточните их по данным Росстата.
        </v-alert>
        <v-card border flat>
          <v-table density="compact">
            <thead>
              <tr>
                <th style="width: 180px">Месяц</th>
                <th style="width: 160px">ИПЦ, %</th>
                <th>Примечание</th>
                <th style="width: 60px" />
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in indexDraft" :key="i">
                <td>
                  <v-text-field v-model="row.period" type="date" density="compact" hide-details variant="plain" />
                </td>
                <td>
                  <v-text-field
                    v-model="row.value"
                    type="number"
                    step="0.01"
                    density="compact"
                    hide-details
                    variant="plain"
                  />
                </td>
                <td>
                  <v-text-field v-model="row.note" density="compact" hide-details variant="plain" />
                </td>
                <td>
                  <v-btn icon="mdi-close" size="x-small" variant="text" @click="indexDraft.splice(i, 1)" />
                </td>
              </tr>
            </tbody>
          </v-table>
          <v-card-actions>
            <v-btn prepend-icon="mdi-plus" class="text-none" @click="addMonth">Добавить месяц</v-btn>
            <span class="text-caption text-medium-emphasis ml-2">
              Накопленный индекс за всю таблицу: × {{ indexTotal.toFixed(4) }}
            </span>
            <v-spacer />
            <v-btn color="primary" variant="flat" class="text-none" :loading="busy" @click="saveIndexes">
              Сохранить таблицу
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-tabs-window-item>
    </v-tabs-window>

    <v-dialog :model-value="!!unitDraft" max-width="560" @update:model-value="unitDraft = null">
      <v-card v-if="unitDraft">
        <v-card-title>{{ unitIsNew ? 'Новая единица' : `Единица ${unitDraft.code}` }}</v-card-title>
        <v-card-text>
          <v-text-field v-model="unitDraft.code" label="Код ОКЕИ" :disabled="!unitIsNew" />
          <v-text-field v-model="unitDraft.name" label="Наименование" />
          <v-text-field v-model="unitDraft.short" label="Обозначение" />
          <v-combobox
            v-model="unitDraft.dimension"
            :items="[...new Set(store.units.map((u) => u.dimension))]"
            label="Размерность"
          />
          <v-text-field
            v-model="unitDraft.factor"
            type="number"
            label="Коэффициент к базовой единице размерности"
          />
          <v-combobox
            v-model="unitDraft.aliases"
            multiple
            chips
            closable-chips
            label="Написания в текстах (Enter после каждого)"
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn class="text-none" @click="unitDraft = null">Отмена</v-btn>
          <v-btn color="primary" variant="flat" class="text-none" :loading="busy" @click="saveUnit">
            Сохранить
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>
