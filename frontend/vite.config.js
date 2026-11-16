import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 백엔드에 CORS 설정이 없어 개발 서버에서 '/api' 요청을 백엔드로 중계한다.
    proxy: {
      '/api': {
        target: 'http://52.78.12.168',
        changeOrigin: true,
      },
    },
  },
})
