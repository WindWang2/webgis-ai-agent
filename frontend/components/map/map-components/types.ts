'use client';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';

export interface RendererContext {
  spec: MapSpec | null;
  zoom: number;
  centerLat: number;
  bearing: number;
  /** U-2（#884）：底部同槽堆叠索引，单组件槽场景可省略 */
  bottomSlotIndexes?: Map<MapSpecComponent, number>;
}

export type ComponentRenderer = (component: MapSpecComponent, ctx: RendererContext) => React.ReactNode;
