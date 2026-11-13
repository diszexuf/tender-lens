// Обновляет контракт API и типы клиента:
//   1) backend/scripts/export_openapi.py → contracts/openapi.json (Python из backend/.venv)
//   2) openapi-typescript → src/api/schema.d.ts
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const backend = resolve(import.meta.dirname, '../../backend')
const python = [
  resolve(backend, '.venv/Scripts/python.exe'),
  resolve(backend, '.venv/bin/python'),
].find(existsSync)

if (!python) {
  console.error('Не найден backend/.venv — создайте окружение backend (см. README, «Разработка»).')
  process.exit(1)
}

execFileSync(python, [resolve(backend, 'scripts/export_openapi.py')], { stdio: 'inherit' })
execFileSync(
  process.execPath,
  [
    resolve(import.meta.dirname, '../node_modules/openapi-typescript/bin/cli.js'),
    resolve(import.meta.dirname, '../../contracts/openapi.json'),
    '-o',
    resolve(import.meta.dirname, '../src/api/schema.d.ts'),
  ],
  { stdio: 'inherit' },
)
