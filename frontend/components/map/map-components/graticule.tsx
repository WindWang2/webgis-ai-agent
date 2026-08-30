'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import {
  graticuleIntervalForZoom,
  graticuleLngLines,
  graticuleLatLines,
} from '@/lib/map-kit/graticule-math';
import type { RendererContext } from './types';

/**
 * Graticule 组件 live 渲染器（P3，补 #1089 deferred 的 live 通道）。
 *
 * 经纬网（anchor 'none' —— 全画布叠加，不参与槽位堆叠；与导出
 * `_drawGraticules` 同语义：同一间隔表/吸附/标签格式，dashed 线 + 角标）。
 * 线位置按真实 map bounds 的**比例**渲染（SVG 百分比坐标）—— 不需要视口
 * 像素尺寸，move 结算（decorState debounce）后自然跟随。
 *
 * bounds 缺席（首次挂载前/无 ctx）→ 不渲染（不虚构网格），下一次
 * move 结算补上。
 */

const LIGHT_LINE = 'rgba(0,0,0,0.12)';
const LIGHT_LABEL = 'rgba(0,0,0,0.35)';
const DARK_LINE = 'rgba(255,255,255,0.15)';
const DARK_LABEL = 'rgba(255,255,255,0.4)';

function GraticuleRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const bounds = ctx.bounds;
  if (
    !bounds
    || !Number.isFinite(bounds.west)
    || !Number.isFinite(bounds.east)
    || !Number.isFinite(bounds.south)
    || !Number.isFinite(bounds.north)
    || bounds.east <= bounds.west
    || bounds.north <= bounds.south
  ) {
    return null;
  }
  const interval = graticuleIntervalForZoom(ctx.zoom);
  const lngLines = graticuleLngLines(bounds.west, bounds.east, interval);
  const latLines = graticuleLatLines(bounds.south, bounds.north, interval);
  const options = (component as unknown as { options?: Record<string, unknown> }).options;
  const color = typeof options?.['color'] === 'string' ? options['color'] : undefined;
  // 主题色缺省时按 variant 二分（light 默认）—— 与导出 dark_mode 分支同色值
  const dark = component.variant === 'geographic' || options?.['dark'] === true;
  const lineColor = color ?? (dark ? DARK_LINE : LIGHT_LINE);
  const labelColor = color ?? (dark ? DARK_LABEL : LIGHT_LABEL);

  return (
    <div
      data-testid="spec-chrome-graticule"
      aria-hidden
      className="pointer-events-none absolute inset-0 z-10"
    >
      <svg className="absolute inset-0 h-full w-full">
        {lngLines.map(({ value, fraction }) => (
          <line
            key={`lng-${value}`}
            x1={`${fraction * 100}%`}
            y1="0%"
            x2={`${fraction * 100}%`}
            y2="100%"
            stroke={lineColor}
            strokeWidth={0.5}
            strokeDasharray="4 4"
          />
        ))}
        {latLines.map(({ value, fraction }) => (
          <line
            key={`lat-${value}`}
            x1="0%"
            y1={`${(1 - fraction) * 100}%`}
            x2="100%"
            y2={`${(1 - fraction) * 100}%`}
            stroke={lineColor}
            strokeWidth={0.5}
            strokeDasharray="4 4"
          />
        ))}
      </svg>
      {/* 角标（经度在底部、纬度在左侧 —— 与导出标签位一致） */}
      {lngLines.map(({ value, fraction, label }) => (
        <span
          key={`lngl-${value}`}
          className="absolute -translate-x-1/2 font-sans"
          style={{
            left: `${fraction * 100}%`,
            bottom: 22,
            color: labelColor,
            fontSize: 9,
          }}
        >
          {label}
        </span>
      ))}
      {latLines.map(({ value, fraction, label }) => (
        <span
          key={`latl-${value}`}
          className="absolute font-sans"
          style={{
            left: 4,
            top: `calc(${(1 - fraction) * 100}% - 12px)`,
            color: labelColor,
            fontSize: 9,
          }}
        >
          {label}
        </span>
      ))}
    </div>
  );
}

registerComponentRenderer('graticule', GraticuleRenderer);
