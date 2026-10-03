import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // 개발 중 /api 요청은 로컬 백엔드(FastAPI)로 넘긴다
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
