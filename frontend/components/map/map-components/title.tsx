'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, resolvePosition, positionStyle } from './helpers';
import type { RendererContext } from './types';

function TitleRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  const text = typeof (component as unknown as { options?: Record<string, unknown> }).options?.['text'] === 'string'
    ? (component as unknown as { options: Record<string, string> }).options['text'] : '';
  if (!text) return null;
  return (
    <div data-testid="spec-chrome-title" className={`map-chrome absolute z-30 max-w-[min(46ch,50%)] truncate px-3 py-1 text-title font-semibold ${positionClass(component)}`}>
      {text}
    </div>
  );
}

function SubtitleRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  const text = typeof (component as unknown as { options?: Record<string, unknown> }).options?.['text'] === 'string'
    ? (component as unknown as { options: Record<string, string> }).options['text'] : '';
  if (!text) return null;
  const pos = resolvePosition(component);
  const style = pos.startsWith('top') ? { top: 'calc(0.75rem + 1.75rem)' } : positionStyle(component);
  return (
    <div data-testid="spec-chrome-subtitle" style={style} className={`map-chrome absolute z-30 max-w-[min(46ch,50%)] truncate px-3 text-caption text-map-chrome-ink-muted ${positionClass(component)}`}>
      {text}
    </div>
  );
}

registerComponentRenderer('title', TitleRenderer);
registerComponentRenderer('subtitle', SubtitleRenderer);
