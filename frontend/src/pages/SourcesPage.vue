<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import SchemaForm from '@/components/SchemaForm.vue'
import { useSourcesStore, type Draft, type Source, type ProbeResult } from '@/stores/sources'
import { SOURCE_STATUS, EXTRACTION, tone, money } from '@/utils/format'

const store = useSourcesStore()
const busy = ref(false),
  error = ref(''),
  editing = ref(false),
  testing = ref(false)
const removing = ref<Source | null>(null),
  editId = ref<string>(),
  probeId = ref<string>()
const query = ref(''),
  result = ref<ProbeResult | null>(null)
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
async function perform(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await action()
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Не удалось выполнить запрос'
  } finally {
    busy.value = false
  }
}
onMounted(() => perform(store.load))
function edit(row?: Source) {
  error.value = ''
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
  error.value = ''
  testing.value = true
}
async function save() {
  await perform(async () => {
    await store.save(draft.value, editId.value)
    editing.value = false
  })
}
async function search() {
  result.value = null
  await perform(async () => {
    result.value = await store.probe(query.value, draft.value, probeId.value)
  })
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
    <v-alert v-if="error && !editing && !testing && !removing" type="error">{{ error }}</v-alert>
    <v-card border flat>
      <v-table>
        <thead>
          <tr>
            <th>Название</th>
            <th>Тип</th>
            <th>Роль</th>
            <th>Приоритет</th>
            <th>Включён</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in store.sources" :key="row.id">
            <td>{{ row.name }}</td>
            <td>{{ row.type }}</td>
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
            <td>
              <v-btn variant="text" size="small" @click="edit(row)">Изменить</v-btn>
              <v-btn variant="text" size="small" @click="openProbe(row.id)">Проверить</v-btn>
              <v-btn
                variant="text"
                size="small"
                color="error"
                @click="confirmDelete(row)"
              >
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
          <v-text-field v-model="draft.name" label="Название" maxlength="200" />
          <v-select
            :model-value="draft.type"
            label="Тип адаптера"
            :items="store.types"
            item-title="title"
            item-value="type"
            @update:model-value="selectType"
          />
          <v-select
            v-model="draft.role"
            label="Роль"
            :items="[
              { title: 'Ценовой', value: 'price' },
              { title: 'Справочный', value: 'reference' },
            ]"
          />
          <v-text-field v-model.number="draft.priority" type="number" min="0" label="Приоритет" />
          <v-switch v-model="draft.enabled" label="Включён" color="primary" />
          <v-combobox
            v-model="draft.okpd2_prefixes"
            label="Префиксы ОКПД2"
            multiple
            chips
            closable-chips
          />
          <SchemaForm
            v-if="selected"
            :schema="selected.config_schema"
            :model-value="draft.config ?? {}"
            @update:model-value="draft.config = $event"
          />
          <v-alert v-if="error && !testing" type="error">{{ error }}</v-alert>
        </v-card-text>
        <v-card-actions>
          <v-btn :disabled="busy" @click="editing = false">Закрыть</v-btn>
          <v-spacer />
          <v-btn :disabled="busy" @click="openProbe()">Проверить</v-btn>
          <v-btn
            color="primary"
            :loading="busy && !testing"
            :disabled="!draft.name.trim()"
            @click="save"
          >
            Сохранить
          </v-btn>
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
            :disabled="busy"
            @keyup.enter="query.trim() && search()"
          />
          <v-alert v-if="error" type="error">{{ error }}</v-alert>
          <template v-if="result">
            <p>
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
