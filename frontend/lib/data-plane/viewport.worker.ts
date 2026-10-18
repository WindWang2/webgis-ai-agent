/**
 * viewport.worker — 数据面视口计算 worker 入口（extreme-scale v2）。
 *
 * 只经 URL 实例化（``new Worker(new URL('./viewport.worker', import.meta.url))``），
 * 绝不在主线程 import —— 自守卫与 mapspec-compiler/reconciler.worker 同款。
 *
 * #1597：只 import 纯协议模块（viewport-compute-protocol），**绝不** import
 * async-viewport-compute（它以 ``new URL`` 引用本文件）—— 主线程模块与
 * worker 入口互引成环，Turbopack 生产构建会无限重算不收敛（CI 20 分钟
 * 超时；webpack 侧表现为 Circular dependency between chunks 警告）。
 */

import { handleViewportComputeRequest } from '@/lib/data-plane/viewport-compute-protocol';

const store = new Map<number, import('@/lib/mapspec-runtime/source-diff').FeatureCollectionLike>();

if (typeof self !== 'undefined' && typeof document === 'undefined') {
  self.onmessage = (event: MessageEvent) => {
    handleViewportComputeRequest(event.data, store, (res) => {
      (self as unknown as Worker).postMessage(res);
    });
  };
}
