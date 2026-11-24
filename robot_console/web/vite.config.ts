// Vite 配置。开发时（npm run dev）网页在 5173 端口，/api 和 /ws 转发给后端（8000 端口）
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

const BACKEND = 'http://127.0.0.1:8000'

export default defineConfig(({ mode }) => ({
  plugins: [vue(), Components({ dirs: [], dts: 'src/element-plus.d.ts', resolvers: [ElementPlusResolver()] }),
    ...(mode === 'console-preview' ? [{
      name: 'console-preview-csp',
      transformIndexHtml() {
        return [{ tag: 'meta', attrs: { 'http-equiv': 'Content-Security-Policy',
          content: "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'" }, injectTo: 'head-prepend' as const }]
      },
    }] : []),
  ],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': BACKEND,
      '/ws': { target: BACKEND, ws: true },
    },
  },
  build: {
    manifest: true,
    chunkSizeWarningLimit: 1000,
  },
}))
