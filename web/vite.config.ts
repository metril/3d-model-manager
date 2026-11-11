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
    // gcode-preview and stats-gl nest their own three (0.159 / 0.170); one copy
    // avoids "Multiple instances of Three.js being imported".
    dedupe: ['three'],
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    // Vite 8 / rolldown: `codeSplitting` groups with explicit priorities (the
    // old `manualChunks` function dragged React's CJS wrapper into
    // vendor-dnd/dockview, which then got modulepreloaded on every page).
    // @dnd-kit is deliberately ungrouped so it lands in the lazy Stats chunk.
    rollupOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: 'vendor-react',
              test: /node_modules[\\/](react|react-dom|scheduler|use-sync-external-store|zustand)[\\/]/,
              priority: 40,
            },
            {
              name: 'vendor-three',
              test: /node_modules[\\/](three|@react-three|three-stdlib|postprocessing|meshline|troika)/,
              priority: 30,
            },
            { name: 'vendor-dockview', test: /node_modules[\\/]dockview/, priority: 20 },
          ],
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
