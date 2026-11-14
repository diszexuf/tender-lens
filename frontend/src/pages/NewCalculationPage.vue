<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useCalculationStore } from '@/stores/calculation'

const router = useRouter()
const store = useCalculationStore()

const text = ref('')
const region = ref<string | null>(null)
const reviewSpec = ref(true)

const EXAMPLES = [
  {
    title: 'Кабель',
    text: 'Кабель силовой ВВГнг(А)-LS 3х2,5 мм², медный, количество жил 3, сечение жилы 2,5 мм². Количество — 500 м.',
  },
  {
    title: 'Картридж',
    text: 'Картридж лазерный для принтеров HP LaserJet Pro P1102/M1132, ресурс не менее 1600 страниц, цвет чёрный, оригинальный или совместимый. Количество — 50 шт.',
  },
  {
    title: 'Канцелярия (3 позиции)',
    text: 'Поставка канцелярских товаров:\n1. Бумага офисная А4, 80 г/м², белизна не менее 146% CIE, 500 листов в пачке — 200 пачек.\n2. Ручка шариковая синяя, толщина линии 0,7 мм — 500 шт.\n3. Скрепки металлические 28 мм, упаковка 100 шт — 100 упаковок.',
  },
]

const valid = computed(() => text.value.trim().length >= 10 && text.value.length <= 20000)

async function submit(): Promise<void> {
  const id = await store.create(text.value.trim(), region.value || null, !reviewSpec.value)
  if (id) await router.push({ name: 'calculation', params: { id } })
}
</script>

<template>
  <section class="page">
    <h1 class="page-title">Новый расчёт НМЦК</h1>

    <v-card border flat>
      <v-card-text>
        <v-textarea
          v-model="text"
          label="Описание закупки"
          placeholder="Наименование, характеристики и количество; можно несколько позиций"
          rows="8"
          auto-grow
          counter="20000"
          variant="outlined"
          :rules="[(v: string) => v.trim().length >= 10 || 'Не меньше 10 символов']"
        />
        <div class="d-flex flex-wrap ga-2 mb-4">
          <span class="text-medium-emphasis text-body-2 align-self-center">Примеры:</span>
          <v-chip
            v-for="ex in EXAMPLES"
            :key="ex.title"
            size="small"
            variant="outlined"
            prepend-icon="mdi-text-box-outline"
            @click="text = ex.text"
          >
            {{ ex.title }}
          </v-chip>
        </div>
        <v-row>
          <v-col cols="12" md="6">
            <v-text-field
              v-model="region"
              label="Регион поставки (необязательно)"
              variant="outlined"
              density="comfortable"
              clearable
            />
          </v-col>
          <v-col cols="12" md="6">
            <v-switch
              v-model="reviewSpec"
              color="primary"
              label="Остановиться после разбора и проверить позиции"
              :hint="
                reviewSpec
                  ? 'Система разберёт текст на позиции и характеристики и покажет их вам. Поиск цен начнётся после подтверждения'
                  : 'Система разберёт текст и сразу начнёт поиск цен, без проверки позиций'
              "
              persistent-hint
            />
          </v-col>
        </v-row>
        <v-alert v-if="store.error" type="error" class="mt-2">{{ store.error }}</v-alert>
      </v-card-text>
      <v-card-actions class="px-4 pb-4">
        <v-spacer />
        <v-btn
          color="primary"
          variant="flat"
          size="large"
          prepend-icon="mdi-play"
          class="text-none"
          :disabled="!valid"
          :loading="store.busy"
          @click="submit"
        >
          {{ reviewSpec ? 'Разобрать описание' : 'Разобрать и рассчитать' }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </section>
</template>
