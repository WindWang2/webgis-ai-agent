'use client';

import React from 'react';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { renderComponent } from './map-components';
import { buildBottomSlotIndexes, stackedBottomStyle } from './map-components/helpers';
import { buildTopSlotIndexes } from './map-components/helpers';
import { metersPerPixelAt } from '@/lib/map-kit/meters-per-pixel';
import { resolveMapComponents } from '@/lib/map-components/resolve-components';
import { composeMapLayout } from '@/lib/layout/compose';
import { useHudStore } from '@/lib/store/useHudStore';

// U-2（#884）同槽堆叠原语经 helpers 供渲染器与测试复用
export { buildBottomSlotIndexes, stackedBottomStyle };

export function computeScale(zoom: number, lat: number): { meters: number; pixels: number } {
  const metersPerPixel = metersPerPixelAt(zoom, lat);
  const targetMeters = metersPerPixel * 100;
  const candidates = [50, 100, 200, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000, 100_000];
  let best = candidates[0];
  for (const c of candidates) if (c <= targetMeters) best = c;
  return { meters: best, pixels: best / metersPerPixel };
}

interface ChromeProps {
  components: MapSpecComponent[];
  zoom: number;
  centerLat: number;
  bearing: number;
  spec: MapSpec | null;
  /** P3：真实地理 bounds（graticule live 渲染；缺席时渲染器自弃）。 */
  bounds?: { west: number; south: number; east: number; north: number };
}

export const MapSpecChrome = React.memo(function MapSpecChrome({ components, zoom, centerLat, bearing, spec, bounds }: ChromeProps) {
  // Workspace V2（Goal C5）：停靠中的面板实例由 PanelDockHost 渲染，
  // chrome 面跳过（同一实例不双渲染）。dock 归属是工作区状态 —— 语义
  // 组件真相（placement/enabled）不受影响。
  const dockPlacements = useHudStore((s) => s.dockPlacements);
  const undocked = components.filter((c) => dockPlacements[c.id] === undefined);
  // ADR-0081：anchor/floating/enabled 的解析经共享 resolveMapComponents
  // （live 与 export 同一语义源 —— 渲染器内部经 helpers 消费同一解析结果）。
  const resolved = resolveMapComponents({ layout: { components: undocked } });
  const enabled = resolved.filter((c) => c.enabled);
  if (!enabled.length) return null;

  // AC-07（ADR-0156）：版面合成 —— 缺项主动补全（P2）+ 冲突自愈轨迹
  // （P1）+ 中间层描述。hasType 口径保持「类型在场即不注入」（含 dock
  // 归属与显式 disabled —— 『不要指南针』语义不变）。
  const dockedTypes = new Set<string>();
  for (const c of components) {
    if (dockPlacements[c.id] !== undefined && typeof c.type === 'string') {
      dockedTypes.add(c.type);
    }
  }
  const composed = composeMapLayout({
    components: undocked, spec, zoom, centerLat, bounds, dockedTypes,
  });
  const renderableRaw = composed.renderable;

  // U-2（#884）：底部同槽组件分层索引（colorbar+scale_bar 不再互压）。
  const bottomSlotIndexes = buildBottomSlotIndexes(renderableRaw);
  // v2(#1079)：顶槽堆叠索引（chart/statistics/annotation 同槽不再互压）
  const topSlotIndexes = buildTopSlotIndexes(renderableRaw);
  const ctx = { spec, zoom, centerLat, bearing, bottomSlotIndexes, topSlotIndexes, bounds };

  return (
    <>
      {renderableRaw.map((c, i) => {
        const node = renderComponent(c, ctx);
        if (!node) return null;
        return <React.Fragment key={`${c.id}#${i}`}>{node}</React.Fragment>;
      })}
    </>
  );
});
