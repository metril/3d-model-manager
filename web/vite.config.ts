import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Stamps a per-build id into dist/sw.js so its cache name changes every deploy.
function stampServiceWorker() {
  return {
    name: 'stamp-service-worker',
    apply: 'build' as const,
    closeBundle() {
      const file = path.resolve(__dirname, 'dist/sw.js')
      try {
        writeFileSync(file, readFileSync(file, 'utf8').replaceAll('__BUILD_ID__', Date.now().toString(36)))
      } catch {
        /* no dist/sw.js (e.g. custom outDir) */
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), stampServiceWorker()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined
          // React core first so three's chunk never owns it (would force-load three eagerly).
          if (/node_modules\/(react|react-dom|scheduler|use-sync-external-store|zustand)\//.test(id)) return 'vendor-react'
          if (/node_modules\/(three|@react-three|three-stdlib|postprocessing|meshline|troika)/.test(id)) return 'vendor-three'
          if (id.includes('node_modules/dockview')) return 'vendor-dockview'
          if (id.includes('node_modules/@dnd-kit')) return 'vendor-dnd'
          return undefined
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
