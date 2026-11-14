<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { CalculationSummary } from '@/api/types'
import { CALC_STATUS, OUTCOME, dateTime, money, tone } from '@/utils/format'

const router = useRouter()
const items = ref<CalculationSummary[]>([])
const total = ref(0)
const loading = ref(false)
const search = ref('')

const headers = [
  { title: 'Дата', key: 'created_at', width: 150, sortable: false },
  { title: 'Закупка', key: 'title', sortable: false },
  { title: 'Поз.', key: 'positions', align: 'end' as const, width: 70, sortable: false },
  { title: 'Статус', key: 'status', width: 200, sortable: false },
  { title: 'НМЦК', key: 'nmck_total', align: 'end' as const, width: 160, sortable: false },
]

async function load(options: { page: number; itemsPerPage: number }): Promise<void> {
  loading.value = true
  try {
    const { data } = await api.GET('/api/calculations', {
      params: {
        query: {
          q: search.value || undefined,
          limit: options.itemsPerPage,
          offset: (options.page - 1) * options.itemsPerPage,
        },
      },
    })
    if (data) {
      items.value = data.items
      total.value = data.total
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="page">
    <h1 class="page-title">История расчётов</h1>
    <v-card border flat>
      <v-card-text>
        <v-text-field
          v-model="search"
          prepend-inner-icon="mdi-magnify"
          label="Поиск по тексту закупки"
          variant="outlined"
          density="compact"
          clearable
          hide-details
          class="mb-3"
        />
        <v-data-table-server
          :headers="headers"
          :items="items"
          :items-length="total"
          :loading="loading"
          :search="search"
          :items-per-page="20"
          hover
          @update:options="load"
          @click:row="(_: unknown, row: { item: CalculationSummary }) => router.push({ name: 'calculation', params: { id: row.item.id } })"
        >
          <template #[`item.created_at`]="{ item }">{{ dateTime(item.created_at) }}</template>
          <template #[`item.status`]="{ item }">
            <v-chip size="small" label :color="tone(CALC_STATUS, item.status).color">
              {{ tone(CALC_STATUS, item.status).label }}
            </v-chip>
          </template>
          <template #[`item.nmck_total`]="{ item }">
            <span v-if="item.nmck_total">{{ money(item.nmck_total) }}</span>
            <v-chip v-else-if="item.outcome" size="x-small" label :color="tone(OUTCOME, item.outcome).color">
              {{ tone(OUTCOME, item.outcome).label }}
            </v-chip>
            <span v-else>—</span>
          </template>
        </v-data-table-server>
      </v-card-text>
    </v-card>
  </section>
</template>

<style scoped>
:deep(tbody tr) {
  cursor: pointer;
}
</style>
