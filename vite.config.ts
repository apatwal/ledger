import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // Split the heaviest vendor libs into their own chunks. Recharts is only
        // pulled in by lazy routes (Dashboard) so it already lands off the initial
        // path; naming it explicitly keeps the split stable, and Clerk (only used
        // when auth is configured) gets its own chunk too.
        manualChunks: {
          recharts: ['recharts'],
          clerk: ['@clerk/react'],
        },
      },
    },
  },
})
