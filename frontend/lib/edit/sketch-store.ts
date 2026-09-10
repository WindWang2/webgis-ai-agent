/**
 * Sketch store — V7 草图编辑的几何真相（模块 store，非 zustand）。
 *
 * 与 selection-store 同款纪律：
 * - 高频交互态（顶点拖拽逐帧更新）不进全局 zustand（避免订阅放大），
 *   组件经 useSyncExternalStore 订阅本模块；
 * - 草图要素以独立 HUD 图层行（`wb-sketch`）挂进图层树 —— 显隐/不透明度/
 *   排序/样式复用既有 mutation 通道，本模块只持有要素集合；
 * - 所有变更经 recordSketchCommand 进 workbench undo 栈（可逆编辑），
 *   or replaceSketchAll（会话恢复/重置）。
 */
import type { Feature, Geometry } from 'geojson';
import type { GeoJSONFeatureCollection } from '@/lib/types';

export type SketchFeature = Feature<Geometry, Record<string, unknown>> & { id: string };

export const SKETCH_LAYER_ID = 'wb-sketch';
export const SKETCH_SOURCE_ID = 'wb-sketch-src';

interface SketchState {
  features: SketchFeature[];
  /** 正在绘制的草稿（未完成要素；完成时才落 features）。 */
  draft: { kind: 'line' | 'polygon'; coordinates: [number, number][] } | null;
  /** 顶点编辑选中的要素 id。 */
  selectedFeatureId: string | null;
  version: number;
}

let state: SketchState = {
  features: [],
  draft: null,
  selectedFeatureId: null,
  version: 0,
};

const listeners = new Set<() => void>();

function emit(): void {
  state = { ...state, version: state.version + 1 };
  listeners.forEach((l) => l());
}

export function subscribeSketch(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getSketchSnapshot(): number {
  return state.version;
}

/** 选择器：读取当前要素集 / 草稿 / 选中态（组件按需组合 memo）。 */
export function getSketchState(): SketchState {
  return state;
}

export function sketchFeatureCollection(): GeoJSONFeatureCollection {
  return { type: 'FeatureCollection', features: state.features } as GeoJSONFeatureCollection;
}

let seq = 0;
export function nextSketchId(): string {
  seq += 1;
  return `sketch-${Date.now().toString(36)}-${seq}`;
}

/** 全量替换（undo 重放 / 会话重置 / cancel 丢弃走这里）。 */
export function replaceSketchFeatures(features: SketchFeature[]): void {
  state = { ...state, features };
  emit();
}

export function setSketchDraft(draft: SketchState['draft']): void {
  state = { ...state, draft };
  emit();
}

export function setSketchSelected(featureId: string | null): void {
  if (state.selectedFeatureId === featureId) return;
  state = { ...state, selectedFeatureId: featureId };
  emit();
}

/** 单要素几何更新（顶点拖拽提交）。目标不存在时 no-op（不换身份/不通知）。 */
export function updateSketchGeometry(featureId: string, geometry: Geometry): void {
  if (!state.features.some((f) => f.id === featureId)) return;
  state = {
    ...state,
    features: state.features.map((f) => (f.id === featureId ? { ...f, geometry } : f)),
  };
  emit();
}

/** 会话/工具复位（不清吸附偏好 —— 那是 toolSlice 的持久偏好）。 */
export function resetSketchStore(): void {
  state = { features: [], draft: null, selectedFeatureId: null, version: state.version };
  emit();
}
