// Type-check and build the frontends of the plugins shipped in ../plugins/<id>/web (each into ../plugins/<id>/ui).
// Usage: npm run build:plugins            all plugins with web/src/main.ts
//        PLUGIN=<id> npm run build:plugins  one plugin
import { spawnSync } from 'node:child_process'
import { existsSync, readdirSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

const web = fileURLToPath(new URL('..', import.meta.url))
const root = fileURLToPath(new URL('../../plugins/', import.meta.url))
const bin = (name) => `${web}node_modules/.bin/${name}`
const ids = process.env.PLUGIN
  ? [process.env.PLUGIN]
  : readdirSync(root).filter((d) => !d.startsWith('_') && existsSync(`${root}${d}/web/src/main.ts`)).sort()

for (const id of ids) {
  console.log(`\n== plugin ${id}`)
  for (const [cmd, args] of [
    [bin('vue-tsc'), ['-p', `${root}${id}/web/tsconfig.json`]],
    [bin('vite'), ['build', '-c', 'vite.plugins.config.ts']],
  ]) {
    const r = spawnSync(cmd, args, { cwd: web, stdio: 'inherit', env: { ...process.env, PLUGIN: id } })
    if (r.status !== 0) process.exit(r.status ?? 1)
  }
}
