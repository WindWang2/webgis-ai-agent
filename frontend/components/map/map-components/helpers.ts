'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

export const POSITION_CLASS: Record<string, string> = {
  'top-left': 'top-3 left-3',
  'top-center': 'top-3 left-1/2 -translate-x-1/2',
  'top-right': 'top-3 right-3',
  'bottom-left': 'bottom-3 left-3',
  'bottom-center': 'bottom-3 left-1/2 -translate-x-1/2',
  'bottom-right': 'bottom-3 right-3',
  none: 'hidden',
};

export const DEFAULT_POSITION: Record<string, string> = {
  title: 'top-center',
  subtitle: 'top-center',
  north_arrow: 'top-right',
  scale_bar: 'bottom-right',
  attribution: 'bottom-left',
  continuous_colorbar: 'bottom-right',
  legend: 'bottom-left',
  categorical_legend: 'bottom-left',
};

export const BOTTOM_OFFSET_STYLE: Record<string, React.CSSProperties> = {
  'bottom-left': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 6px)' },
  'bottom-center': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 6px)' },
  'bottom-right': { bottom: 'calc(var(--map-chrome-bottom, 10px) + 30px)' },
};

export function resolvePosition(component: MapSpecComponent): string {
  return (component as unknown as { position?: string }).position ?? DEFAULT_POSITION[component.type] ?? 'none';
}

export function positionClass(component: MapSpecComponent): string {
  const pos = resolvePosition(component);
  return POSITION_CLASS[pos] ?? POSITION_CLASS[DEFAULT_POSITION[component.type] ?? 'none'] ?? 'hidden';
}

export function positionStyle(component: MapSpecComponent): React.CSSProperties | undefined {
  return BOTTOM_OFFSET_STYLE[resolvePosition(component)];
}
