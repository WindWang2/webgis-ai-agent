import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'url'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./test/setup.ts'],
    include: ['**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: [
        'node_modules/',
        '.next/',
        'test/',
        '*.config.*'
      ]
    }
  },
  resolve: {
    alias: {
      // module: esnext 下没有 __dirname，用 import.meta.url 推导前端根目录
      // （原 // @ts-nocheck + __dirname 的写法在 ESLint 9 下被
      // @typescript-eslint/ban-ts-comment 拦截，已改为此等价写法）。
      '@': fileURLToPath(new URL('./', import.meta.url))
    },
    extensions: ['.ts', '.tsx', '.js', '.jsx', '.json']
  }
})
