<script setup lang="ts">
import { computed } from 'vue'
interface Schema {
  type?: string
  title?: string
  description?: string
  default?: unknown
  enum?: string[]
  properties?: Record<string, Schema>
  $defs?: Record<string, Schema>
  $ref?: string
  anyOf?: Schema[]
  minimum?: number
  maximum?: number
}
const props = defineProps<{ schema: Schema; root?: Schema }>()
const model = defineModel<Record<string, unknown>>({ required: true })
const rootSchema = computed(() => props.root ?? props.schema)
function resolve(field: Schema): Schema {
  const inner = field.anyOf?.find((s) => s.type !== 'null') ?? field
  const ref = rootSchema.value.$defs?.[inner.$ref?.split('/').pop() ?? '']
  return { ...ref, ...inner, ...field }
}
const fields = computed(() =>
  Object.entries(props.schema.properties ?? {}).map(([key, raw]) => {
    const schema = resolve(raw)
    return {
      key,
      schema,
      label: schema.title ?? key,
      value: key in model.value ? model.value[key] : schema.default,
    }
  }),
)
function enumValue(value: unknown) {
  return typeof value === 'string' ? value : null
}
function set(key: string, value: unknown) {
  model.value = { ...model.value, [key]: value }
}
</script>
<template>
  <div v-for="{ key, schema: field, label, value } in fields" :key="key" class="mb-2">
    <fieldset v-if="field.properties" class="pa-3 rounded">
      <legend>{{ label }}</legend>
      <SchemaForm
        :schema="field"
        :root="rootSchema"
        :model-value="(value ?? {}) as Record<string, unknown>"
        @update:model-value="set(key, $event)"
      />
    </fieldset>
    <v-select
      v-else-if="field.enum"
      :label="label"
      :hint="field.description"
      persistent-hint
      :items="field.enum"
      :model-value="enumValue(value)"
      @update:model-value="set(key, $event)"
    />
    <v-switch
      v-else-if="field.type === 'boolean'"
      :label="label"
      :hint="field.description"
      persistent-hint
      :model-value="Boolean(value)"
      @update:model-value="set(key, $event)"
    />
    <v-combobox
      v-else-if="field.type === 'array'"
      multiple
      chips
      closable-chips
      :label="label"
      :hint="field.description"
      persistent-hint
      :model-value="(value ?? []) as string[]"
      @update:model-value="set(key, $event)"
    />
    <v-text-field
      v-else-if="field.type === 'number' || field.type === 'integer'"
      type="number"
      :min="field.minimum"
      :max="field.maximum"
      :label="label"
      :hint="field.description"
      persistent-hint
      :model-value="value"
      @update:model-value="set(key, $event === '' ? null : Number($event))"
    />
    <v-text-field
      v-else
      :label="label"
      :hint="field.description"
      persistent-hint
      :model-value="value"
      @update:model-value="
        set(key, $event || (field.anyOf?.some((s) => s.type === 'null') ? null : ''))
      "
    />
  </div>
</template>
