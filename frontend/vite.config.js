import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 업무 API와 문서·상태 경로는 SPA fallback보다 먼저 백엔드로 보낸다.
    proxy: {
      '^/(api(?:/|\\?|$)|docs(?:/|\\?|$)|(?:health|ready|openapi\\.json)(?:\\?|$))': {
        target: process.env.SLF_API_TARGET || 'http://52.78.12.168',
        changeOrigin: true,
      },
    },
  },
})
