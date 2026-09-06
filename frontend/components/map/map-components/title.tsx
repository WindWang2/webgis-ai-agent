'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, resolvePosition, positionStyle, resolveVariant } from './helpers';
import type { RendererContext } from './types';

// V3（ADR-0101 D3）标题变体 —— 与 descriptor.variants / component templates
// 同词表：minimal（轻量）/ academic（缺省，现状样式）/ report（加粗强调）/
// presentation（大字演示）/ government（公文加宽字距）。未知变体确定性
// 回退 academic（与 legend 族约定一致，不崩 chrome）。
const TITLE_VARIANT_CLASS: Record<string, string> = {
  minimal: 'text-caption font-medium tracking-normal',
  academic: 'text-title font-semibold tracking-normal',
  report: 'text-title font-bold tracking-normal',
  presentation: 'text-heading font-bold tracking-normal',
  government: 'text-title font-bold tracking-widest',
};

function TitleRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  const text = typeof (component as unknown as { options?: Record<string, unknown> }).options?.['text'] === 'string'
    ? (component as unknown as { options: Record<string, string> }).options['text'] : '';
  if (!text) return null;
  const variant = resolveVariant(component, 'academic');
  const variantClass = TITLE_VARIANT_CLASS[variant] ?? TITLE_VARIANT_CLASS.academic;
  return (
    <div data-testid="spec-chrome-title" data-variant={variant} className={`map-chrome absolute z-30 max-w-[min(46ch,50%)] truncate px-3 py-1 ${variantClass} ${positionClass(component)}`}>
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
  const variant = resolveVariant(component, 'default');
  const sizeClass = variant === 'report' || variant === 'academic' ? 'text-micro' : 'text-caption';
  return (
    <div data-testid="spec-chrome-subtitle" data-variant={variant} style={style} className={`map-chrome absolute z-30 max-w-[min(46ch,50%)] truncate px-3 ${sizeClass} text-map-chrome-ink-muted ${positionClass(component)}`}>
      {text}
    </div>
  );
}

registerComponentRenderer('title', TitleRenderer);
registerComponentRenderer('subtitle', SubtitleRenderer);
