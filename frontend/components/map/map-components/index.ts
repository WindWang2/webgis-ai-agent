'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import type { RendererContext } from './types';
import { getComponentRenderer } from './registry';

// side-effect: register all built-in renderers
import './title';
import './north-arrow';
import './scale-bar';
import './attribution';
import './colorbar';
import './legends';

export function renderComponent(component: MapSpecComponent, ctx: RendererContext): React.ReactNode {
  const renderer = getComponentRenderer(component.type);
  if (!renderer) {
    // graceful degradation: unknown component type — render nothing, do not crash chrome
    if (process.env.NODE_ENV !== 'production') {
      console.warn(`[map-components] No renderer for component type: ${component.type}`);
    }
    return null;
  }
  try {
    return renderer(component, ctx);
  } catch (e) {
    console.error(`[map-components] Renderer failed for ${component.type}`, e);
    return null;
  }
}
