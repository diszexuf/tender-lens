<script setup lang="ts">
const items = [
  { title: 'Новый расчёт', icon: 'mdi-plus', to: '/calculations/new' },
  { title: 'История', icon: 'mdi-history', to: '/calculations' },
  { title: 'Источники', icon: 'mdi-database-search', to: '/sources' },
  { title: 'Настройки', icon: 'mdi-cog', to: '/settings' },
  { title: 'Состояние', icon: 'mdi-heart-pulse', to: '/status' },
]
</script>

<template>
  <v-app>
    <v-app-bar flat border="b" density="comfortable">
      <template #prepend>
        <router-link to="/" class="brand">
          <v-icon icon="mdi-magnify-scan" color="primary" />
          <span>Tender Lens</span>
        </router-link>
      </template>

      <v-btn
        v-for="item in items"
        :key="item.to"
        :to="item.to"
        :prepend-icon="item.icon"
        variant="text"
        class="text-none"
      >
        {{ item.title }}
      </v-btn>
    </v-app-bar>

    <v-main>
      <router-view />
    </v-main>
  </v-app>
</template>

<style scoped>
.brand {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0 1.5rem 0 0.5rem;
  font-size: 1.125rem;
  font-weight: 700;
  color: rgb(var(--v-theme-primary));
  text-decoration: none;
}
</style>
