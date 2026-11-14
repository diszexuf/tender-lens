<script setup lang="ts">
import { computed } from 'vue'
import { fieldError, isBasic, isRequired, resolveField, type Schema } from '@/utils/schema'

/** only: basic — обязательные и основные поля, advanced — остальные, без значения — все */
const props = defineProps<{
  schema: Schema & { 'x-enum-titles'?: Record<string, string> }
  root?: Schema
  only?: 'basic' | 'advanced'
}>()
const model = defineModel<Record<string, unknown>>({ required: true })
const rootSchema = computed(() => props.root ?? props.schema)
const fields = computed(() =>
  Object.entries(props.schema.properties ?? {})
    .map(([key, raw]) => {
      const schema = resolveField(raw, rootSchema.value) as Schema & {
        'x-enum-titles'?: Record<string, string>
      }
      const required = isRequired(props.schema, key)
      return {
        key,
        schema,
        required,
        basic: isBasic(props.schema, key, schema),
        label: (schema.title ?? key) + (required ? ' *' : ''),
        value: key in model.value ? model.value[key] : schema.default,
      }
    })
    .filter((f) => !props.only || (props.only === 'basic') === f.basic),
)
function enumItems(field: Schema & { 'x-enum-titles'?: Record<string, string> }) {
  return (field.enum ?? []).map((v) => ({ title: field['x-enum-titles']?.[v] ?? v, value: v }))
}
function enumValue(value: unknown) {
  return typeof value === 'string' ? value : null
}
function set(key: string, value: unknown) {
  model.value = { ...model.value, [key]: value }
}
function rules(field: Schema, required: boolean) {
  return [(v: unknown) => fieldError(field, v, required) ?? true]
}
</script>
<template>
  <div
    v-for="{ key, schema: field, label, value, required } in fields"
    :key="key"
    class="mb-2"
  >
    <fieldset v-if="field.properties" class="pa-3 rounded">
      <legend>{{ label }}</legend>
      <p v-if="field.description" class="text-caption mb-2">{{ field.description }}</p>
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
      :items="enumItems(field)"
      :model-value="enumValue(value)"
      @update:model-value="set(key, $event)"
    />
    <v-switch
      v-else-if="field.type === 'boolean'"
      :label="label"
      :hint="field.description"
      persistent-hint
      color="primary"
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
      :rules="rules(field, required)"
      :model-value="value"
      @update:model-value="set(key, $event === '' ? null : Number($event))"
    />
    <v-text-field
      v-else
      :label="label"
      :hint="field.description"
      persistent-hint
      :rules="rules(field, required)"
      :model-value="value"
      @update:model-value="
        set(key, $event || (field.anyOf?.some((s) => s.type === 'null') ? null : ''))
      "
    />
  </div>
</template>
