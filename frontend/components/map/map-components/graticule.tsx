'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import {
  graticuleLngLines,
  graticuleLatLines,
} from '@/lib/map-kit/graticule-math';
import type { RendererContext } from './types';
import { selectGraticuleInterval } from '@/lib/layout/graticule-density';
import {
  formatGraticuleLabel,
  frameAnnotations,
  graticuleLabelFormatForSpan,
} from '@/lib/layout/graticule-labels';

/**
 * Graticule 组件 live 渲染器（P3，补 #1089 deferred 的 live 通道）。
 *
 * 经纬网（anchor 'none' —— 全画布叠加，不参与槽位堆叠；与导出
 * `_drawGraticules` 同语义：dashed 线 + 角标）。线位置按真实 map bounds
 * 的**比例**渲染（SVG 百分比坐标）—— 不需要视口像素尺寸，move 结算
 * （decorState debounce）后自然跟随。
 *
 * AC-07（ADR-0156 P4/P5）：
 * - 密度自适应：跨度优选序列使网格线数 ∈ [3,10]；options.interval 显式
 *   指定优先；无 bounds 回退既有 zoom 表。
 * - 图廓注记：四角经纬度标注（格式随跨度 deg/dm/dms 自适应）——
 *   options.showFrameLabels === false 可关。
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
  const options = (component as unknown as { options?: Record<string, unknown> }).options;
  const explicitInterval = typeof options?.['interval'] === 'number'
    ? (options['interval'] as number)
    : undefined;
  const density = selectGraticuleInterval(
    bounds.east - bounds.west,
    bounds.north - bounds.south,
    { explicitIntervalDeg: explicitInterval, zoom: ctx.zoom },
  );
  const interval = density.intervalDeg;
  const lngLines = graticuleLngLines(bounds.west, bounds.east, interval);
  const latLines = graticuleLatLines(bounds.south, bounds.north, interval);
  const color = typeof options?.['color'] === 'string' ? options['color'] : undefined;
  // 主题色缺省时按 variant 二分（light 默认）—— 与导出 dark_mode 分支同色值
  const dark = component.variant === 'geographic' || options?.['dark'] === true;
  const lineColor = color ?? (dark ? DARK_LINE : LIGHT_LINE);
  const labelColor = color ?? (dark ? DARK_LABEL : LIGHT_LABEL);
  // P4：图廓注记格式随跨度自适应 + 四角标注
  const spanDeg = Math.max(bounds.east - bounds.west, bounds.north - bounds.south);
  const labelFormat = graticuleLabelFormatForSpan(spanDeg);
  const showFrameLabels = options?.['showFrameLabels'] !== false;
  const frame = showFrameLabels
    ? frameAnnotations(bounds, labelFormat)
    : [];
  // lng/lat 线标签改用统一格式器（dm/dms 档下与角注记同形）
  const lngLabel = (v: number) => formatGraticuleLabel(v, labelFormat, false);
  const latLabel = (v: number) => formatGraticuleLabel(v, labelFormat, true);

  return (
    <div
      data-testid="spec-chrome-graticule"
      data-density-source={density.source}
      data-label-format={labelFormat}
      data-line-count-lng={density.lineCountLng}
      data-line-count-lat={density.lineCountLat}
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
      {lngLines.map(({ value, fraction }) => (
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
          {lngLabel(value)}
        </span>
      ))}
      {latLines.map(({ value, fraction }) => (
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
          {latLabel(value)}
        </span>
      ))}
      {/* 图廓四角注记（nw/ne/sw/se —— 经度上/下缘、纬度左/右缘） */}
      {frame.map((f) => (
        <React.Fragment key={`frame-${f.position}`}>
          <span
            className="absolute font-sans"
            style={{
              color: labelColor,
              fontSize: 9,
              ...(f.position.startsWith('n') ? { top: 2 } : { bottom: 2 }),
              ...(f.position.endsWith('w') ? { left: 4 } : { right: 4 }),
            }}
          >
            {f.lngLabel}
          </span>
          <span
            className="absolute font-sans"
            style={{
              color: labelColor,
              fontSize: 9,
              writingMode: 'vertical-rl',
              ...(f.position.startsWith('n') ? { top: 4 } : { bottom: 4 }),
              ...(f.position.endsWith('w') ? { left: 2 } : { right: 2 }),
            }}
          >
            {f.latLabel}
          </span>
        </React.Fragment>
      ))}
    </div>
  );
}

registerComponentRenderer('graticule', GraticuleRenderer);
