/** JSON Schema формы настроек (Pydantic model_json_schema) и проверка значений до отправки. */
export interface Schema {
  type?: string
  title?: string
  description?: string
  default?: unknown
  enum?: string[]
  properties?: Record<string, Schema>
  required?: string[]
  $defs?: Record<string, Schema>
  $ref?: string
  anyOf?: Schema[]
  minimum?: number
  maximum?: number
  pattern?: string
  /** Поле показывается в основной части формы, остальные — в «Дополнительно» */
  'x-basic'?: boolean
  /** Понятное сообщение, если значение не подходит под pattern */
  'x-pattern-message'?: string
}

export function resolveField(field: Schema, root: Schema): Schema {
  const inner = field.anyOf?.find((s) => s.type !== 'null') ?? field
  const ref = root.$defs?.[inner.$ref?.split('/').pop() ?? '']
  return { ...ref, ...inner, ...field }
}

export function isRequired(schema: Schema, key: string): boolean {
  return schema.required?.includes(key) ?? false
}

/** Основное поле: обязательное или отмеченное x-basic */
export function isBasic(schema: Schema, key: string, field: Schema): boolean {
  return isRequired(schema, key) || field['x-basic'] === true
}

function empty(value: unknown): boolean {
  return value === undefined || value === null || (typeof value === 'string' && !value.trim())
}

/** Ошибка значения поля или null */
export function fieldError(field: Schema, value: unknown, required: boolean): string | null {
  if (empty(value)) return required ? 'Обязательное поле' : null
  if (field.pattern && typeof value === 'string' && !new RegExp(field.pattern).test(value))
    return field['x-pattern-message'] ?? 'Неверный формат'
  if (typeof value === 'number') {
    if (field.minimum !== undefined && value < field.minimum) return `Не меньше ${field.minimum}`
    if (field.maximum !== undefined && value > field.maximum) return `Не больше ${field.maximum}`
  }
  return null
}

/** Все ошибки формы: «Название поля: ошибка» */
export function schemaErrors(
  schema: Schema,
  value: Record<string, unknown>,
  root: Schema = schema,
): string[] {
  const out: string[] = []
  for (const [key, raw] of Object.entries(schema.properties ?? {})) {
    const field = resolveField(raw, root)
    const v = key in value ? value[key] : field.default
    if (field.properties) {
      out.push(...schemaErrors(field, (v ?? {}) as Record<string, unknown>, root))
      continue
    }
    const err = fieldError(field, v, isRequired(schema, key))
    if (err) out.push(`${field.title ?? key}: ${err}`)
  }
  return out
}
