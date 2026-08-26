'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { isFloating, placementStyle, positionClass } from './helpers';
import type { RendererContext } from './types';

/**
 * annotation 渲染器：options.text 的样式化注释卡。
 * 纯静态标注（无交互壳）；floating placement 经 placementStyle 直出。
 */
function AnnotationRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  const text = typeof component.options?.['text'] === 'string'
    ? (component.options['text'] as string)
    : '';
  if (!text.trim()) return null;
  const floating = isFloating(component);
  return (
    <div
      data-testid="spec-chrome-annotation"
      className={`map-chrome absolute z-30 max-w-[min(40ch,60%)] rounded-chrome border-l-2 border-l-map-chrome-ink px-2.5 py-1.5 text-caption leading-relaxed text-map-chrome-ink-muted ${floating ? '' : positionClass(component)}`}
      style={floating ? placementStyle(component) : undefined}
    >
      {text}
    </div>
  );
}

registerComponentRenderer('annotation', AnnotationRenderer);
