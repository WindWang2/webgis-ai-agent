'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, resolveVariant, stackedBottomStyle } from './helpers';
import type { RendererContext } from './types';
import { metersPerPixelAt } from '@/lib/map-kit/meters-per-pixel';
import { computeNiceScale } from '@/lib/map-kit/scale-math';
import { numericScaleAt, scaleDisplayMode } from '@/lib/layout/numeric-scale';

// ADR-0084（E-3）：与导出共用同一 nice-number 算法（scale-math.ts）——
// 此前 live 用固定候选表、export 用 nice-number，同一 zoom 标出不同距离。
function computeScale(zoom: number, lat: number) {
  const mpp = metersPerPixelAt(zoom, lat);
  const { meters, px } = computeNiceScale(mpp, 100);
  return { meters, pixels: px };
}
function formatMeters(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(m % 1000 === 0 ? 0 : 1)} km` : `${m} m`;
}

// V3（ADR-0101 D3）：dual_unit 变体的英制换算 —— 与公制同一根比例尺条
// （同 px 宽度），按该段实际代表距离换算 ft/mi，不是独立第二根尺。
function formatImperial(meters: number): string {
  const feet = meters * 3.28084;
  if (feet >= 5280) {
    const miles = feet / 5280;
    return `${miles.toFixed(miles >= 10 ? 0 : 1)} mi`;
  }
  return `${Math.round(feet)} ft`;
}

// D7：academic —— 黑白交替分段尺（经典制图比例尺），4 段等分。
function AcademicSegments({ pixels }: { pixels: number }) {
  const segments = 4;
  const segWidth = Math.max(2, Math.round(pixels / segments));
  return (
    <div aria-hidden className="flex border border-map-chrome-ink" style={{ height: '7px' }}>
      {Array.from({ length: segments }, (_, i) => (
        <div
          key={i}
          style={{ width: `${segWidth}px`, background: i % 2 === 0 ? 'var(--map-chrome-text)' : 'transparent' }}
        />
      ))}
    </div>
  );
}

function ScaleBarRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const { meters, pixels } = computeScale(ctx.zoom, ctx.centerLat);
  // AC-07（ADR-0156 P3）：数字（比率式）比例尺 1:N —— 与图形条并存
  // （默认 both，可配 bar/numeric 二选一）。N 随 zoom+纬度（cos 修正）。
  const displayMode = scaleDisplayMode(component.options as Record<string, unknown> | undefined);
  const numeric = numericScaleAt(ctx.zoom, ctx.centerLat);
  // D7：minimal（缺省，现状）| boxed（卡片）| academic（黑白分段）
  // V3：dual_unit（公制主行 + 英制换算行，同一根比例尺条）
  const variant = resolveVariant(component, 'minimal');
  const width = Math.round(pixels);
  const numericTag = (
    <span
      data-testid="spec-chrome-scale-numeric"
      className="text-micro tabular-nums text-map-chrome-ink-muted"
    >
      {numeric.label}
    </span>
  );
  return (
    <div
      data-testid="spec-chrome-scale-bar"
      data-variant={variant}
      data-scale-display={displayMode}
      style={stackedBottomStyle(component, ctx.bottomSlotIndexes)}
      className={`map-chrome absolute z-30 flex items-center gap-2 text-caption font-medium tabular-nums ${positionClass(component)} ${
        variant === 'boxed' ? 'rounded-chrome px-2.5 py-1.5' : variant === 'academic' ? 'rounded-chrome px-2 py-1' : 'px-2 py-1'
      }`}
      aria-label={
        displayMode === 'numeric'
          ? `比例尺 ${numeric.label}`
          : `比例尺 ${formatMeters(meters)}${displayMode === 'both' ? `，${numeric.label}` : ''}${variant === 'dual_unit' ? `（${formatImperial(meters)}）` : ''}`
      }
    >
      {displayMode === 'numeric' ? (
        numericTag
      ) : variant === 'academic' || variant === 'dual_unit' ? (
        <div className={variant === 'dual_unit' ? 'flex flex-col gap-0.5' : 'flex items-center gap-2'}>
          <div className="flex items-center gap-2">
            <span className="text-micro tabular-nums text-map-chrome-ink-muted">0</span>
            <AcademicSegments pixels={pixels} />
            <span className="text-map-chrome-ink">{formatMeters(meters)}</span>
          </div>
          {variant === 'dual_unit' ? (
            <span className="text-micro tabular-nums text-map-chrome-ink-muted">{formatImperial(meters)}</span>
          ) : null}
          {displayMode === 'both' ? numericTag : null}
        </div>
      ) : (
        <>
          <div aria-hidden className="border-b-2 border-l-2 border-r-2 border-map-chrome-ink" style={{ width: `${width}px`, height: '5px' }} />
          <span className="text-map-chrome-ink">{formatMeters(meters)}</span>
          {displayMode === 'both' ? numericTag : null}
        </>
      )}
    </div>
  );
}

registerComponentRenderer('scale_bar', ScaleBarRenderer);
