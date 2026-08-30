'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { isFloating, placementStyle, positionClass, resolveVariant, stackedTopStyle } from './helpers';
import type { RendererContext } from './types';
import { anchorFractionInBounds, bboxToBounds, validBounds, type GeoBounds } from '@/lib/map-components/geo-anchor';

/**
 * inset_map 渲染器（v2 P1）：轻量区位插图。
 *
 * 刻意**不 mount 第二个 maplibre.Map runtime**（内存/生命周期/导出 parity
 * 三重代价）—— 插图是纯 SVG 静态投影：
 * - options.bbox：插图地理范围（缺省自弃 —— 不虚构范围）；
 * - options.mainBbox：主图范围指示框；缺席时回退真实 map bounds
 *   （ctx.bounds，graticule 同源 —— 自动确定主图可视范围）；
 * - options.boundary：可选边界折线（≤512 点，如行政区/国界概略轮廓 ——
 *   由 Agent/后端从既有 artifact 简化传入，前端不加载几何服务）；
 * - variant：overview（范围框 + 指示）/ location（同型，标题强调区位）。
 *
 * live 与 export 共享 geo-anchor 投影语义（同一 bounds 线性插值）——
 * 指示框位置两侧一致，导出不静默消失（ADR-0081 parity 契约）。
 */

/** 插图盒尺寸（逻辑 px；有界固定 —— 不做任意尺寸攻击面）。 */
const INSET_W = 176;
const INSET_H = 132;
const INSET_PAD = 10;

/** 边界折线渲染上限（与后端 MAX_INSET_BOUNDARY_POINTS 同值）。 */
const MAX_BOUNDARY_POINTS = 512;

interface InsetOptions {
  bbox: GeoBounds;
  mainBbox?: GeoBounds;
  boundary?: [number, number][];
  label?: string;
}

/** bbox 顶点范围（boundary 可能超出 bbox 时取并集，避免轮廓被裁剪）。 */
function dataBounds(b: GeoBounds, boundary?: [number, number][]): GeoBounds {
  if (!boundary || boundary.length === 0) return b;
  let { west, south, east, north } = b;
  for (const [lng, lat] of boundary) {
    if (Number.isFinite(lng) && Number.isFinite(lat)) {
      west = Math.min(west, lng);
      east = Math.max(east, lng);
      south = Math.min(south, lat);
      north = Math.max(north, lat);
    }
  }
  return { west, south, east, north };
}

/** bbox → 盒内等比适配矩形（保持经纬纵横比，居中）。 */
function fitProjection(bounds: GeoBounds, w: number, h: number) {
  const spanLng = bounds.east - bounds.west;
  const spanLat = bounds.north - bounds.south;
  if (!(spanLng > 0) || !(spanLat > 0)) return null;
  const scale = Math.min(w / spanLng, h / spanLat);
  const drawW = spanLng * scale;
  const drawH = spanLat * scale;
  const offX = (w - drawW) / 2;
  const offY = (h - drawH) / 2;
  const project = (lng: number, lat: number): [number, number] => [
    offX + (lng - bounds.west) * scale,
    offY + (bounds.north - lat) * scale, // y 向下翻转（北在上）
  ];
  return { project, drawW, drawH, scale };
}

function parseOptions(component: MapSpecComponent): InsetOptions | null {
  const options = (component.options ?? {}) as Record<string, unknown>;
  const bbox = bboxToBounds(options['bbox']);
  if (!bbox) return null;
  const rawMain = bboxToBounds(options['mainBbox']);
  const rawBoundary = options['boundary'];
  const boundary = Array.isArray(rawBoundary)
    ? (rawBoundary
        .slice(0, MAX_BOUNDARY_POINTS)
        .filter((pt): pt is [number, number] =>
          Array.isArray(pt) && pt.length === 2 &&
          Number.isFinite(Number(pt[0])) && Number.isFinite(Number(pt[1])))
        .map((pt) => [Number(pt[0]), Number(pt[1])] as [number, number]))
    : undefined;
  return {
    bbox,
    mainBbox: rawMain ?? undefined,
    boundary: boundary && boundary.length >= 3 ? boundary : undefined,
    label: typeof options['label'] === 'string' ? options['label'] : undefined,
  };
}

function InsetMapRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const parsed = parseOptions(component);
  if (!parsed) return null;

  // 主图范围：显式 mainBbox > 真实 map bounds（自动确定 —— 指示框跟随
  // 当前视口；无 bounds（测试/首次挂载）→ 不画指示框，只画范围示意）。
  const mainBounds = parsed.mainBbox
    ?? (validBounds(ctx.bounds ?? null) ? (ctx.bounds as GeoBounds) : undefined);

  const innerW = INSET_W - INSET_PAD * 2;
  const innerH = INSET_H - INSET_PAD * 2;
  const data = dataBounds(parsed.bbox, parsed.boundary);
  const proj = fitProjection(data, innerW, innerH);
  if (!proj) return null;

  const boundaryPath = parsed.boundary
    ? parsed.boundary
        .map(([lng, lat]) => proj.project(lng, lat).join(','))
        .join(' ')
    : '';

  // 指示框：主图 bbox 四角投影 → SVG rect（相交裁剪到插图范围）
  let indicator: { x: number; y: number; w: number; h: number } | null = null;
  if (mainBounds) {
    const nw = proj.project(
      Math.max(Math.min(mainBounds.east, data.east), data.west),
      Math.min(Math.max(mainBounds.north, data.south), data.north),
    );
    const se = proj.project(
      Math.min(Math.max(mainBounds.west, data.west), data.east),
      Math.max(Math.min(mainBounds.south, data.south), data.north),
    );
    const x = Math.min(nw[0], se[0]);
    const y = Math.min(nw[1], se[1]);
    indicator = { x, y, w: Math.abs(se[0] - nw[0]), h: Math.abs(se[1] - nw[1]) };
    if (indicator.w < 2 || indicator.h < 2) indicator = null; // 退化 → 不画
  }

  const variant = resolveVariant(component, 'overview');
  const floating = isFloating(component);
  const label = parsed.label ?? (variant === 'location' ? '区位' : '概览');

  return (
    <div
      data-testid="spec-chrome-inset-map"
      data-variant={variant}
      className={`map-chrome absolute z-30 rounded-chrome px-2 py-1.5 ${floating ? '' : positionClass(component)}`}
      style={floating ? placementStyle(component) : stackedTopStyle(component, ctx.topSlotIndexes)}
      aria-label={`区位插图：${label}`}
    >
      <div className="mb-0.5 text-micro font-medium text-map-chrome-ink">{label}</div>
      <svg
        width={INSET_W}
        height={INSET_H}
        viewBox={`0 0 ${INSET_W} ${INSET_H}`}
        role="img"
        aria-hidden
        className="rounded-sm border border-map-chrome-border bg-[rgba(127,127,127,0.08)]"
      >
        {/* 边界轮廓（有界折线；无几何数据时仅画范围框 —— 如实表达） */}
        {boundaryPath && (
          <polygon
            points={boundaryPath}
            fill="rgba(127,127,127,0.18)"
            stroke="rgba(127,127,127,0.55)"
            strokeWidth={1}
          />
        )}
        {/* 经纬十字（bbox 中点，示意比例 —— 非网格） */}
        <line
          x1={INSET_PAD} y1={INSET_H / 2} x2={INSET_W - INSET_PAD} y2={INSET_H / 2}
          stroke="rgba(127,127,127,0.25)" strokeWidth={0.5} strokeDasharray="3 3"
        />
        <line
          x1={INSET_W / 2} y1={INSET_PAD} x2={INSET_W / 2} y2={INSET_H - INSET_PAD}
          stroke="rgba(127,127,127,0.25)" strokeWidth={0.5} strokeDasharray="3 3"
        />
        {/* 主图范围指示框 */}
        {indicator && (
          <rect
            x={indicator.x} y={indicator.y}
            width={indicator.w} height={indicator.h}
            fill="rgba(225,29,72,0.14)"
            stroke="#e11d48"
            strokeWidth={1.5}
            rx={1}
          />
        )}
      </svg>
    </div>
  );
}

registerComponentRenderer('inset_map', InsetMapRenderer);
