import createClient from 'openapi-fetch'

import type { components, paths } from './schema'

// Запросы идут на тот же origin: в Docker /api проксирует nginx, в dev — Vite.
export const api = createClient<paths>({ baseUrl: '' })

export type Schemas = components['schemas']
