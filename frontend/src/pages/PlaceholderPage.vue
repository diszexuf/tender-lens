<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <section class="page">
    <h1 class="page-title">{{ route.meta.title }}</h1>
    <v-alert type="info" icon="mdi-progress-wrench">
      Раздел появится на этапе {{ route.meta.stage }} плана реализации.
    </v-alert>
  </section>
</template>
