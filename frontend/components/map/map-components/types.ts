'use client';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';

export interface RendererContext {
  spec: MapSpec | null;
  zoom: number;
  centerLat: number;
  bearing: number;
}

export type ComponentRenderer = (component: MapSpecComponent, ctx: RendererContext) => React.ReactNode;
