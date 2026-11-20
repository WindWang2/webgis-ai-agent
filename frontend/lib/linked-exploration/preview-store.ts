/**
 * G46 最近一次组合 preview 回执的模块级共享缓存。
 *
 * 作者面板触发 preview（单一请求点），exploration_chart_panel 组件实例从
 * 这里读各自 chart 的 facet 统计渲染 —— 不发第二份请求。世代与
 * condition-store generation 对齐：条件变化 → 面板重发 → 覆盖这里。
 */
import type { ChartPreview, PreviewResponse } from './contracts';

let data: PreviewResponse | null = null;
let at = 0;
const listeners = new Set<() => void>();
let generation = 0;

export function setPreviewData(next: PreviewResponse | null): void {
  data = next;
  at = Date.now();
  generation += 1;
  listeners.forEach((l) => l());
}

export function getPreviewData(): PreviewResponse | null {
  return data;
}

export function getChartPreview(chartId: string): ChartPreview | null {
  return data?.charts.find((c) => c.chart_id === chartId) ?? null;
}

export function getPreviewGeneration(): number {
  return generation;
}

export function getPreviewAt(): number {
  return at;
}

export function subscribePreview(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
