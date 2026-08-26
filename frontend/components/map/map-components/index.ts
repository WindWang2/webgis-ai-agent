'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import type { RendererContext } from './types';
import { getComponentRenderer } from './registry';
import { devOnly } from '@/lib/utils/logger';

// side-effect: register all built-in renderers
import './title';
import './north-arrow';
import './scale-bar';
import './attribution';
import './colorbar';
import './legends';
import './annotation';
import './statistics-panel';
import './chart-panel';

export function renderComponent(component: MapSpecComponent, ctx: RendererContext): React.ReactNode {
  const renderer = getComponentRenderer(component.type);
  if (!renderer) {
    // graceful degradation: unknown component type — render nothing, do not crash chrome
    // #1008：裸 console.warn → devOnly（生产不泄漏组件类型等内部细节）。
    devOnly.warn(`[map-components] No renderer for component type: ${component.type}`);
    return null;
  }
  try {
    return renderer(component, ctx);
  } catch (e) {
    // #1008：renderer 异常此前无条件 console.error（生产也打）→ devOnly。
    devOnly.error(`[map-components] Renderer failed for ${component.type}`, e);
    return null;
  }
}
