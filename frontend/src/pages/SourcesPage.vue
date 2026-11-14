<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import SchemaForm from '@/components/SchemaForm.vue'
import { useSourcesStore, type Draft, type Source, type ProbeResult } from '@/stores/sources'
import { SOURCE_STATUS, EXTRACTION, tone, money, dateTime } from '@/utils/format'
import { schemaErrors, type Schema } from '@/utils/schema'

const store = useSourcesStore()
const busy = ref(false),
  error = ref(''), // ошибка страницы: список, включение, сброс, удаление
  formError = ref(''), // ошибка окна источника
  probeError = ref(''), // ошибка окна проверки
  editing = ref(false),
  testing = ref(false)
const removing = ref<Source | null>(null),
  editId = ref<string>(),
  probeId = ref<string>()
const query = ref(''),
  result = ref<ProbeResult | null>(null)
const advanced = ref<number[]>([])
function blank(): Draft {
  return {
    name: '',
    type: 'html',
    role: 'price',
    enabled: true,
    priority: 100,
    config: {},
    okpd2_prefixes: [],
  }
}
const draft = ref<Draft>(blank())
const selected = computed(() => store.types.find((t) => t.type === draft.value.type))
const configSchema = computed(() => selected.value?.config_schema as Schema | undefined)
// Незаполненные поля настройки адаптера: без них проверка и сохранение невозможны
const configProblems = computed(() =>
  configSchema.value
    ? schemaErrors(configSchema.value, (draft.value.config ?? {}) as Record<string, unknown>)
    : ['Выберите, что подключаем'],
)
const problems = computed(() => [
  ...(draft.value.name.trim() ? [] : ['Название: Обязательное поле']),
  ...configProblems.value,
])

async function perform(action: () => Promise<void>, target = error) {
  if (busy.value) return
  busy.value = true
  target.value = ''
  try {
    await action()
  } catch (e) {
    target.value = e instanceof Error ? e.message : 'Не удалось выполнить запрос'
  } finally {
    busy.value = false
  }
}
onMounted(() => perform(store.load))
function edit(row?: Source) {
  formError.value = ''
  advanced.value = []
  editId.value = row?.id
  draft.value = row
    ? {
        ...blank(),
        name: row.name,
        type: row.type,
        role: row.role as Draft['role'],
        priority: row.priority,
        enabled: row.enabled,
        config: JSON.parse(JSON.stringify(row.config)),
        okpd2_prefixes: [...row.okpd2_prefixes],
      }
    : blank()
  editing.value = true
}
function confirmDelete(row: Source) {
  error.value = ''
  removing.value = row
}
function selectType(type: string) {
  draft.value.type = type
  draft.value.config = {}
}
function openProbe(id?: string) {
  probeId.value = id
  query.value = ''
  result.value = null
  probeError.value = ''
  testing.value = true
}
async function save() {
  await perform(async () => {
    await store.save(draft.value, editId.value)
    editing.value = false
  }, formError)
}
async function search() {
  result.value = null
  await perform(async () => {
    result.value = await store.probe(query.value, draft.value, probeId.value)
  }, probeError)
}
async function remove() {
  if (removing.value)
    await perform(async () => {
      await store.remove(removing.value!.id)
      removing.value = null
    })
}
</script>
<template>
  <section class="page">
    <div class="d-flex align-center justify-space-between mb-4">
      <h1 class="page-title">Источники</h1>
      <v-btn color="primary" prepend-icon="mdi-plus" :disabled="busy" @click="edit()">
        Добавить
      </v-btn>
    </div>
    <v-alert
      v-if="error && !removing"
      type="error"
      closable
      class="mb-4"
      @click:close="error = ''"
    >
      {{ error }}
    </v-alert>
    <v-card border flat>
      <v-table>
        <thead>
          <tr>
            <th>Название</th>
            <th>Тип</th>
            <th>Роль</th>
            <th>Приоритет</th>
            <th>Включён</th>
            <th>Состояние</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in store.sources" :key="row.id">
            <td>{{ row.name }}</td>
            <td>{{ store.types.find((t) => t.type === row.type)?.title ?? row.type }}</td>
            <td>{{ row.role === 'price' ? 'Ценовой' : 'Справочный' }}</td>
            <td>{{ row.priority }}</td>
            <td>
              <v-switch
                hide-details
                color="primary"
                :aria-label="'Включён: ' + row.name"
                :disabled="busy"
                :model-value="row.enabled"
                @update:model-value="perform(() => store.enable(row.id, !row.enabled))"
              />
            </td>
            <td class="text-caption">
              <template v-if="store.health[row.id]">
                <v-chip
                  v-if="store.health[row.id]!.circuit_open"
                  size="x-small"
                  label
                  color="warning"
                  prepend-icon="mdi-electric-switch-closed"
                  class="mb-1"
                >
                  отключён на {{ Math.ceil((store.health[row.id]!.retry_in_s ?? 0) / 60) }} мин
                </v-chip>
                <div>Успех: {{ dateTime(store.health[row.id]!.last_success_at) }}</div>
                <div v-if="store.health[row.id]!.error_share !== null">
                  Ошибок: {{ Math.round((store.health[row.id]!.error_share ?? 0) * 100) }} %
                  из {{ store.health[row.id]!.runs }}
                </div>
                <v-btn
                  v-if="store.health[row.id]!.circuit_open || store.health[row.id]!.consecutive_failures"
                  size="x-small"
                  variant="text"
                  class="text-none px-0"
                  :disabled="busy"
                  @click="perform(() => store.resetBreaker(row.id))"
                >
                  Сбросить ошибки
                </v-btn>
              </template>
            </td>
            <td>
              <v-btn variant="text" size="small" @click="edit(row)">Изменить</v-btn>
              <v-btn variant="text" size="small" @click="openProbe(row.id)">Проверить</v-btn>
              <v-btn variant="text" size="small" color="error" @click="confirmDelete(row)">
                Удалить
              </v-btn>
            </td>
          </tr>
        </tbody>
      </v-table>
    </v-card>

    <v-dialog v-model="editing" max-width="720" scrollable :persistent="busy">
      <v-card>
        <v-card-title>{{ editId ? 'Настройки источника' : 'Новый источник' }}</v-card-title>
        <v-card-text>
          <p class="text-body-2 mb-4">
            Поля со звёздочкой (*) обязательны. Остальное можно не менять.
          </p>
          <v-select
            :model-value="draft.type"
            label="Что подключаем *"
            :items="store.types"
            item-title="title"
            item-value="type"
            :hint="selected?.description"
            persistent-hint
            class="mb-2"
            @update:model-value="selectType"
          />
          <v-text-field
            v-model="draft.name"
            label="Название *"
            hint="Как источник будет называться в отчёте, например «ОфисМаг»"
            persistent-hint
            maxlength="200"
            :rules="[(v: string) => !!v.trim() || 'Обязательное поле']"
            class="mb-2"
          />
          <SchemaForm
            v-if="configSchema"
            only="basic"
            :schema="configSchema"
            :model-value="draft.config ?? {}"
            @update:model-value="draft.config = $event"
          />
          <v-switch
            v-model="draft.enabled"
            label="Использовать в расчётах"
            color="primary"
            hide-details
          />
          <v-expansion-panels v-model="advanced" multiple flat class="mt-2">
            <v-expansion-panel>
              <v-expansion-panel-title class="px-0">
                Дополнительно (для настройки разработчиком)
              </v-expansion-panel-title>
              <v-expansion-panel-text>
                <v-select
                  v-model="draft.role"
                  label="Роль"
                  hint="Справочный источник не участвует в расчёте цены"
                  persistent-hint
                  :items="[
                    { title: 'Ценовой', value: 'price' },
                    { title: 'Справочный', value: 'reference' },
                  ]"
                  class="mb-2"
                />
                <v-text-field
                  v-model.number="draft.priority"
                  type="number"
                  min="0"
                  label="Приоритет"
                  hint="Меньше — опрашивается раньше"
                  persistent-hint
                  class="mb-2"
                />
                <v-combobox
                  v-model="draft.okpd2_prefixes"
                  label="Префиксы ОКПД2"
                  hint="Только для позиций с такими кодами; пусто — для всех"
                  persistent-hint
                  multiple
                  chips
                  closable-chips
                  class="mb-2"
                />
                <SchemaForm
                  v-if="configSchema"
                  only="advanced"
                  :schema="configSchema"
                  :model-value="draft.config ?? {}"
                  @update:model-value="draft.config = $event"
                />
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>
          <v-alert v-if="formError" type="error" class="mt-2">{{ formError }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-btn :disabled="busy" @click="editing = false">Закрыть</v-btn>
          <v-spacer />
          <v-tooltip :disabled="!configProblems.length" location="top">
            <template #activator="{ props: tip }">
              <span v-bind="tip">
                <v-btn :disabled="busy || configProblems.length > 0" @click="openProbe()">
                  Проверить
                </v-btn>
              </span>
            </template>
            <div v-for="p in configProblems" :key="p">{{ p }}</div>
          </v-tooltip>
          <v-tooltip :disabled="!problems.length" location="top">
            <template #activator="{ props: tip }">
              <span v-bind="tip">
                <v-btn
                  color="primary"
                  :loading="busy && !testing"
                  :disabled="problems.length > 0"
                  @click="save"
                >
                  Сохранить
                </v-btn>
              </span>
            </template>
            <div v-for="p in problems" :key="p">{{ p }}</div>
          </v-tooltip>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="testing" max-width="700" :persistent="busy" scrollable>
      <v-card>
        <v-card-title>Проверить источник</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="query"
            label="Название товара без персональных данных"
            hint="Например: бумага офисная А4"
            persistent-hint
            :disabled="busy"
            @keyup.enter="query.trim() && search()"
          />
          <v-alert v-if="probeError" type="error" class="mt-2">{{ probeError }}</v-alert>
          <template v-if="result">
            <p class="mt-2">
              {{ tone(SOURCE_STATUS, result.status).label }} ·
              {{ (result.duration_ms / 1000).toFixed(1) }} с
            </p>
            <v-alert v-if="result.error" type="warning">{{ result.error }}</v-alert>
            <p v-for="note in result.warnings" :key="note">{{ note }}</p>
            <v-card
              v-for="offer in result.offers"
              :key="offer.url"
              variant="outlined"
              class="pa-3 my-2"
            >
              <a :href="offer.url" target="_blank" rel="noopener noreferrer">{{ offer.title }}</a>
              <div>
                {{
                  offer.currency === 'RUB'
                    ? money(offer.price)
                    : [offer.price, offer.currency].join(' ')
                }}
                {{ offer.unit_raw }} ·
                {{ EXTRACTION[offer.extraction_method] ?? offer.extraction_method }}
              </div>
            </v-card>
          </template>
        </v-card-text>
        <v-card-actions>
          <v-btn :disabled="busy" @click="testing = false">Закрыть</v-btn>
          <v-spacer />
          <v-btn color="primary" :loading="busy" :disabled="!query.trim()" @click="search">
            Проверить
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    <v-dialog
      :model-value="!!removing"
      max-width="500"
      :persistent="busy"
      @update:model-value="removing = null"
    >
      <v-card>
        <v-card-title>Удалить источник?</v-card-title>
        <v-card-text>
          {{ removing?.name }}
          <v-alert v-if="error" type="error">{{ error }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-btn :disabled="busy" @click="removing = null">Отмена</v-btn>
          <v-btn color="error" :loading="busy" @click="remove">Удалить</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </section>
</template>
