'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, stackedBottomStyle } from './helpers';
import type { RendererContext } from './types';

function AttributionRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  const text = typeof (component as unknown as { options?: Record<string, unknown> }).options?.['text'] === 'string'
    ? (component as unknown as { options: Record<string, string> }).options['text'] : '';
  if (!text) return null;
  return (
    <div data-testid="spec-chrome-attribution" style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 px-2 py-0.5 text-micro text-map-chrome-ink-muted ${positionClass(component)}`}>
      {text}
    </div>
  );
}

registerComponentRenderer('attribution', AttributionRenderer);
