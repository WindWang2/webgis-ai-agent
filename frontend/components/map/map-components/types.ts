'use client';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';

export interface RendererContext {
  spec: MapSpec | null;
  zoom: number;
  centerLat: number;
  bearing: number;
  /** P3：真实 bounds（graticule 等全画布地理叠加用；缺席则渲染器自弃）。 */
  bounds?: { west: number; south: number; east: number; north: number };
  /** U-2（#884）：底部同槽堆叠索引，单组件槽场景可省略 */
  bottomSlotIndexes?: Map<MapSpecComponent, number>;
  /** v2(Phase 9, #1079)：顶部同槽堆叠索引（chart/statistics/annotation） */
  topSlotIndexes?: Map<MapSpecComponent, number>;
}

export type ComponentRenderer = (component: MapSpecComponent, ctx: RendererContext) => React.ReactNode;
