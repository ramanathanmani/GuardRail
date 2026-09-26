import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const port = process.env.BACKEND_PORT || '8000'
const target = `http://localhost:${port}`
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': target,
      '/v1': target,
      '/vobiz': target,
      '/health': target,
      '/ws': { target: `ws://localhost:${port}`, ws: true },
    },
  },
})
