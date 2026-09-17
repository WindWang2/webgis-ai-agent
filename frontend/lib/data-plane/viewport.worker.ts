/**
 * viewport.worker — 数据面视口计算 worker 入口（extreme-scale v2）。
 *
 * 只经 URL 实例化（``new Worker(new URL('./viewport.worker', import.meta.url))``），
 * 绝不在主线程 import —— 自守卫与 mapspec-compiler/reconciler.worker 同款。
 */

import { handleViewportComputeRequest } from '@/lib/data-plane/async-viewport-compute';

const store = new Map<number, import('@/lib/mapspec-runtime/source-diff').FeatureCollectionLike>();

if (typeof self !== 'undefined' && typeof document === 'undefined') {
  self.onmessage = (event: MessageEvent) => {
    handleViewportComputeRequest(event.data, store, (res) => {
      (self as unknown as Worker).postMessage(res);
    });
  };
}
