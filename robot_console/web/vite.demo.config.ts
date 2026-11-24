// Vite configuration of the static web demo (demo/index.html): `npm run dev:demo` / `npm run build:demo`.
// The demo replays recorded data from demo/public/data/ and needs no backend, so it can be hosted on any
// static web server (GitHub Pages: .github/workflows/demo.yml). Output: dist-demo/.
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  root: fileURLToPath(new URL('./demo', import.meta.url)),
  base: './', // relative asset paths: the page works under any sub-path (e.g. /<repo>/ on GitHub Pages)
  plugins: [vue(), Components({ dirs: [], dts: false, resolvers: [ElementPlusResolver()] })],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    host: '127.0.0.1',
    port: 5175,
    strictPort: true,
  },
  build: {
    outDir: fileURLToPath(new URL('./dist-demo', import.meta.url)),
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
})
