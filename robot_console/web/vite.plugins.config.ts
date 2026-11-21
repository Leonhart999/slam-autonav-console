// Builds the frontends of the plugins shipped in ../plugins/<id>/web into ../plugins/<id>/ui (one IIFE script and
// one style sheet each). vue and element-plus are not bundled: plugins use the console's own copies through
// window.RobotConsole (docs/plugins.md). Run through scripts/build-plugins.mjs (npm run build:plugins), which sets PLUGIN.
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const id = process.env.PLUGIN ?? 'ai_assistant'
const root = fileURLToPath(new URL(`../plugins/${id}`, import.meta.url))

export default defineConfig({
  plugins: [vue()],
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: `${root}/ui`,
    emptyOutDir: true,
    copyPublicDir: false,
    cssCodeSplit: false,
    minify: true,
    lib: {
      entry: `${root}/web/src/main.ts`,
      formats: ['iife'],
      name: `RobotConsolePlugin_${id}`,
      fileName: () => 'plugin.js',
      cssFileName: 'plugin',
    },
    rollupOptions: {
      external: ['vue', 'element-plus'],
      output: { globals: { vue: 'RobotConsole.vue', 'element-plus': 'RobotConsole.ui' } },
    },
  },
})
