import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  plugins: [
    vue(),
    // Автоимпорт только используемых компонентов Vuetify (tree-shaking)
    vuetify({ autoImport: true }),
  ],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      // В режиме разработки API доступен на localhost:8000 (см. API_PORT в .env)
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
