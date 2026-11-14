<script setup lang="ts">
import { ref, watch } from 'vue'

import type { PositionSpec, SpecAttribute } from '@/api/types'
import { ATTR_TYPES, OPERATORS } from '@/utils/format'

const props = defineProps<{
  positions: PositionSpec[]
  warnings: string[]
  busy: boolean
  submitLabel?: string
}>()
const emit = defineEmits<{ run: [positions: PositionSpec[]] }>()

type EditablePosition = Omit<PositionSpec, 'attributes'> & { attributes: SpecAttribute[] }

const draft = ref<EditablePosition[]>([])
watch(
  () => props.positions,
  (value) => {
    draft.value = (JSON.parse(JSON.stringify(value)) as PositionSpec[]).map((p) => ({
      ...p,
      attributes: p.attributes ?? [],
      exclude_words: p.exclude_words ?? [],
    }))
  },
  { immediate: true },
)

const NUMBER_OPS = ['eq', 'gte', 'lte'].map((v) => ({ value: v, title: OPERATORS[v] }))

function newAttr(): SpecAttribute {
  return {
    name: '',
    type: 'enum',
    operator: 'eq',
    values: [],
    required: true,
    value_number: null,
    value_min: null,
    value_max: null,
    value_bool: null,
    value_text: null,
    unit: null,
  }
}

function newPosition(): EditablePosition {
  return {
    ordinal: draft.value.length + 1,
    name: '',
    quantity: '1',
    unit_raw: 'шт',
    search_query: '',
    attributes: [],
    okei_code: null,
    ktru_code: null,
    ktru_confirmed: false,
    exclude_words: [],
  }
}

function onTypeChange(attr: SpecAttribute): void {
  attr.operator = attr.type === 'range' ? 'between' : 'eq'
}

function submit(): void {
  const positions = draft.value.map((p, i) => ({
    ...p,
    ordinal: i + 1,
    attributes: p.attributes.filter((a) => a.name.trim()),
  }))
  emit('run', positions)
}
</script>

<template>
  <div>
    <v-alert v-for="w in warnings" :key="w" type="warning" density="compact" class="mb-2">
      {{ w }}
    </v-alert>

    <v-card v-for="(p, pi) in draft" :key="pi" border flat class="mb-4">
      <v-card-item>
        <v-card-title class="d-flex align-center ga-2 text-body-1 font-weight-medium">
          <v-avatar color="primary" size="28" variant="tonal">{{ pi + 1 }}</v-avatar>
          Позиция
          <v-spacer />
          <v-btn
            icon="mdi-delete-outline"
            variant="text"
            size="small"
            :disabled="draft.length === 1"
            @click="draft.splice(pi, 1)"
          />
        </v-card-title>
      </v-card-item>
      <v-card-text>
        <v-row dense>
          <v-col cols="12" md="6">
            <v-text-field v-model="p.name" label="Наименование" variant="outlined" density="compact" />
          </v-col>
          <v-col cols="6" md="2">
            <v-text-field
              v-model="p.quantity"
              label="Количество"
              type="number"
              variant="outlined"
              density="compact"
            />
          </v-col>
          <v-col cols="6" md="2">
            <v-text-field v-model="p.unit_raw" label="Единица" variant="outlined" density="compact" />
          </v-col>
          <v-col cols="12" md="2">
            <v-text-field
              v-model="p.okei_code"
              label="Код ОКЕИ"
              variant="outlined"
              density="compact"
              hint="Определён автоматически"
            />
          </v-col>
          <v-col cols="12">
            <v-text-field
              v-model="p.search_query"
              label="Поисковая фраза"
              variant="outlined"
              density="compact"
              hint="По ней ищутся предложения в источниках"
              persistent-hint
            />
          </v-col>
          <v-col cols="12">
            <v-combobox
              v-model="p.exclude_words"
              label="Не брать предложения, если в названии есть"
              variant="outlined"
              density="compact"
              multiple
              chips
              closable-chips
              hint="Начало слова, Enter после каждого: «полимерн» исключит скрепки с полимерным покрытием. Сувенирные и подарочные товары исключаются всегда (список — в «Настройках»)"
              persistent-hint
            />
          </v-col>
        </v-row>

        <div class="text-subtitle-2 mt-4 mb-2">Характеристики</div>
        <v-table density="compact" class="attr-table">
          <thead>
            <tr>
              <th style="width: 22%">Название</th>
              <th style="width: 16%">Тип</th>
              <th>Требование</th>
              <th style="width: 10%">Ед.</th>
              <th style="width: 12%">Обязательная</th>
              <th style="width: 48px" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="(a, ai) in p.attributes" :key="ai">
              <td><v-text-field v-model="a.name" variant="plain" density="compact" hide-details /></td>
              <td>
                <v-select
                  v-model="a.type"
                  :items="ATTR_TYPES"
                  variant="plain"
                  density="compact"
                  hide-details
                  @update:model-value="onTypeChange(a)"
                />
              </td>
              <td>
                <div v-if="a.type === 'number'" class="d-flex ga-2">
                  <v-select
                    v-model="a.operator"
                    :items="NUMBER_OPS"
                    variant="plain"
                    density="compact"
                    hide-details
                    style="max-width: 80px"
                  />
                  <v-text-field v-model="a.value_number" type="number" variant="plain" density="compact" hide-details />
                </div>
                <div v-else-if="a.type === 'range'" class="d-flex ga-2">
                  <v-text-field v-model="a.value_min" label="от" type="number" variant="plain" density="compact" hide-details />
                  <v-text-field v-model="a.value_max" label="до" type="number" variant="plain" density="compact" hide-details />
                </div>
                <v-combobox
                  v-else-if="a.type === 'enum'"
                  v-model="a.values"
                  multiple
                  chips
                  closable-chips
                  variant="plain"
                  density="compact"
                  hide-details
                  placeholder="Допустимые значения"
                />
                <v-switch
                  v-else-if="a.type === 'bool'"
                  v-model="a.value_bool"
                  :label="a.value_bool ? 'да' : 'нет'"
                  color="primary"
                  density="compact"
                  hide-details
                />
                <v-text-field v-else v-model="a.value_text" variant="plain" density="compact" hide-details />
              </td>
              <td><v-text-field v-model="a.unit" variant="plain" density="compact" hide-details /></td>
              <td><v-checkbox v-model="a.required" color="primary" density="compact" hide-details /></td>
              <td>
                <v-btn icon="mdi-close" variant="text" size="x-small" @click="p.attributes.splice(ai, 1)" />
              </td>
            </tr>
          </tbody>
        </v-table>
        <v-btn
          variant="text"
          size="small"
          prepend-icon="mdi-plus"
          class="text-none mt-1"
          @click="p.attributes.push(newAttr())"
        >
          Добавить характеристику
        </v-btn>
      </v-card-text>
    </v-card>

    <div class="d-flex ga-2">
      <v-btn variant="tonal" prepend-icon="mdi-plus" class="text-none" @click="draft.push(newPosition())">
        Добавить позицию
      </v-btn>
      <v-spacer />
      <v-btn
        color="primary"
        variant="flat"
        prepend-icon="mdi-magnify"
        class="text-none"
        :loading="busy"
        :disabled="!draft.length || draft.some((p) => !p.name.trim())"
        @click="submit"
      >
        {{ submitLabel ?? 'Запустить поиск и расчёт' }}
      </v-btn>
    </div>
  </div>
</template>

<style scoped>
.attr-table td {
  vertical-align: middle;
}
</style>
