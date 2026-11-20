/**
 * G27 契约测试的最小可复跑配置（不加载 jsdom / react 插件 / coverage）。
 *
 * 用途：在 `ulimit -v` 受限（4 GiB 虚拟地址）的宿主上，Vite 默认工具链
 * 会因 WebAssembly.instantiate() 无法保留内存而失败；本配置只跑纯 TS
 * 契约用例（backend/frontend 共消费同一 corpus），不替代主 vitest.config.ts
 * 的完整功能/性能车道，也不降低任何门禁阈值。
 *
 * 复跑：npx vitest run --config vitest.g27.config.ts
 */
import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'url';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['lib/carto-ir/scale-portrayal.test.ts'],
    globals: true,
    pool: 'forks',
    maxWorkers: 1,
    setupFiles: [],
    coverage: { enabled: false },
  },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./', import.meta.url)) },
    extensions: ['.ts', '.tsx', '.js', '.json'],
  },
});
