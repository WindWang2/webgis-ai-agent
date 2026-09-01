import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'url'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    testTimeout: 15000,
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
      ],
      // #564：此前只有 reporter/exclude，无 thresholds —— 覆盖闸是装饰性的
      // （任何覆盖率都能绿）。按当前基线（~78% lines / ~72% functions，
      // 2026-08 在 master bb09e8c 上实测）ratchet，略低于实测值留出抖动
      // 余量；随测试补充逐步调高。branches 在 jsdom 下计数不完整，下限放宽。
      thresholds: {
        lines: 75,
        functions: 70,
        statements: 75,
        branches: 60
      }
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
