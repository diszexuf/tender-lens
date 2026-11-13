import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'
import pluginVue from 'eslint-plugin-vue'

export default defineConfigWithVueTs(
  { name: 'app/files', files: ['**/*.{ts,mts,vue}'] },
  { name: 'app/ignores', ignores: ['dist/**', 'node_modules/**', 'src/api/schema.d.ts'] },
  pluginVue.configs['flat/essential'], // стилистические правила не включаем,
  vueTsConfigs.recommended,
)
