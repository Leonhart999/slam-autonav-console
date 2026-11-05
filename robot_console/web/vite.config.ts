// Vite 配置。开发时（npm run dev）网页在 5173 端口，/api 和 /ws 转发给后端（8000 端口）
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const BACKEND = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
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
    chunkSizeWarningLimit: 2000, // Element Plus 整体引入后包比较大，本机使用不影响
  },
})
