'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { resolveVariant } from './helpers';

/**
 * Map Border 组件 live 渲染器（P6, ADR-0084）。
 *
 * 全画布图框（anchor 'none' —— 不参与槽位堆叠；位于 chrome 层之下、
 * 数据层之上，pointer-events:none）。三变体与导出 drawChromeMapBorder
 * 同语义：
 * - minimal：单细框；
 * - academic：双框（外粗内细）+ 四角刻度（经典制图图廓）；
 * - report：粗外框 + 细内框（报告封面风）。
 */
function MapBorderRenderer(component: MapSpecComponent) {
  const variant = resolveVariant(component, 'minimal');
  const options = (component as unknown as { options?: Record<string, unknown> }).options;
  const color = typeof options?.['color'] === 'string' ? options['color'] : undefined;
  const ink = color ?? 'var(--map-chrome-ink, #1e293b)';
  const inset = variant === 'minimal' ? 8 : 10;
  const common = 'pointer-events-none absolute z-20' as const;
  const style: React.CSSProperties = {
    inset,
    borderColor: ink,
    borderStyle: 'solid',
  };

  if (variant === 'academic') {
    return (
      <div data-testid="spec-chrome-map-border" aria-hidden>
        <div
          className={common}
          style={{ ...style, borderWidth: 2, borderRadius: 2 }}
        />
        <div
          className={common}
          style={{ ...style, inset: inset + 4, borderWidth: 1 }}
        />
        {/* 四角刻度：外框角向内 12px 的短线段（与导出四角 tick 同位） */}
        {[
          { top: inset, left: inset },
          { top: inset, right: inset },
          { bottom: inset, left: inset },
          { bottom: inset, right: inset },
        ].map((pos, i) => (
          <div
            key={i}
            className={common}
            style={{
              ...pos,
              width: 12,
              height: 12,
              borderColor: ink,
              borderStyle: 'solid',
              borderTopWidth: i < 2 ? 3 : 0,
              borderBottomWidth: i >= 2 ? 3 : 0,
              borderLeftWidth: i % 2 === 0 ? 3 : 0,
              borderRightWidth: i % 2 === 1 ? 3 : 0,
            }}
          />
        ))}
      </div>
    );
  }

  if (variant === 'report') {
    return (
      <div data-testid="spec-chrome-map-border" aria-hidden>
        <div className={common} style={{ ...style, inset, borderWidth: 3 }} />
        <div className={common} style={{ ...style, inset: inset + 5, borderWidth: 1 }} />
      </div>
    );
  }

  return (
    <div
      data-testid="spec-chrome-map-border"
      aria-hidden
      className={common}
      style={{ ...style, borderWidth: 1, borderRadius: 2 }}
    />
  );
}

registerComponentRenderer('map_border', MapBorderRenderer);
