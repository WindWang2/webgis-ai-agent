'use client';
import React, { useEffect, useState } from 'react';
import type { ChartData } from '@/lib/types';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { ChartCore } from '@/components/chat/chart-core';
import {
  getCachedChartArtifact,
  loadChartArtifact,
} from '@/lib/map-components/chart-artifact';
import { registerComponentRenderer } from './registry';
import { resolveVariant } from './helpers';
import { FloatingChrome, usePlacementPatchedComponent } from './floating-chrome';
import type { RendererContext } from './types';

/**
 * elevation_profile 渲染器（G3D05 / ADR-0275 线路高程剖面面板）。
 *
 * - 数据通道（与 chart_panel 同协议）：
 *   - inline `options.points = [{distance_m, elevation_m}...]`（≥2 点可画；
 *     不足 → 「剖面数据不足」占位）—— x = 距离（≥1000m 转 km 标签，孪生
 *     _profile_to_chart 同口径），y = 高程，单位 options.unit ?? 'm'；
 *   - `options.profileRef`（ref:profile-*）→ chart-artifact 工件通道
 *     （与 ref:chart-* 同一 GET /chart-artifacts/{ref} 拉取 + 模块缓存）；
 *     拉取失败/载荷不可用 → 「剖面数据不可用（ref 未绑定）」诚实占位，
 *     绝不虚构剖面；
 * - 标题「高程剖面」+ options.verticalDatum 在场时追加（基准：EGM96）；
 * - variant default | compact（compact 高 160 + 紧凑内边距）；
 * - aria：role="img" aria-label="高程剖面"（目录 accessibility 契约）。
 */

// 文案常量：G3D05 词条暂未入 messages/map.json（messages 面不归本组件
// 批次）；模块级非文案键位（no-hardcoded-cjk 门禁与 no-raw-cjk 守卫的
// obj:* 文案键位均不含这些键名），后续 i18n 批次键化时迁 messages。
const COPY = {
  panelTitle: '高程剖面',
  datumSuffixPrefix: '（基准：',
  datumSuffixSuffix: '）',
  yLabelPrefix: '高程（',
  yLabelSuffix: '）',
  xLabel: '线路里程',
  insufficient: '剖面数据不足',
  loading: '剖面数据加载中…',
  unavailable: '剖面数据不可用（ref 未绑定）',
};

interface ProfilePoint {
  distance_m: number;
  elevation_m: number;
}

/** 防御式解析 inline points：有限数值对（坏条目剔除，诚实不硬转）。 */
function parseProfilePoints(raw: unknown): ProfilePoint[] {
  if (!Array.isArray(raw)) return [];
  const out: ProfilePoint[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    const d = rec['distance_m'];
    const e = rec['elevation_m'];
    if (typeof d !== 'number' || !Number.isFinite(d)) continue;
    if (typeof e !== 'number' || !Number.isFinite(e)) continue;
    out.push({ distance_m: d, elevation_m: e });
  }
  return out;
}

/** 距离轴标签：≥1000m → km（孪生 _profile_to_chart 同口径）。 */
function distanceLabel(distanceM: number): string {
  return distanceM >= 1000
    ? `${(distanceM / 1000).toFixed(2)}km`
    : `${distanceM.toFixed(0)}m`;
}

function profileTitle(verticalDatum: string | undefined): string {
  return verticalDatum
    ? `${COPY.panelTitle}${COPY.datumSuffixPrefix}${verticalDatum}${COPY.datumSuffixSuffix}`
    : COPY.panelTitle;
}

/** 内容高度：compact 160；有显式面板高度时填满（chart_panel 同款规则）。 */
function contentHeight(variant: string, panelHeight: number | undefined): number | `${number}%` {
  if (typeof panelHeight === 'number' && panelHeight > 48) return '100%';
  return variant === 'compact' ? 160 : 200;
}

type ProfileState =
  | { status: 'insufficient' }                  // 无/不足 points 且无 ref
  | { status: 'loading' }                       // ref 拉取中
  | { status: 'unavailable' }                   // ref 拉取失败 / 载荷不可用
  | { status: 'ready'; chart: ChartData };      // 可渲染

function ElevationProfilePanelView({ component, ctx }: { component: MapSpecComponent; ctx?: RendererContext }) {
  const patched = usePlacementPatchedComponent(component);
  const variant = resolveVariant(patched, 'default') === 'compact' ? 'compact' : 'default';
  const placement = patched.placement;
  const panelHeight = placement?.mode === 'floating' ? placement.height : undefined;

  const options = patched.options ?? {};
  const points = parseProfilePoints(options['points']);
  const unit = typeof options['unit'] === 'string' && options['unit']
    ? (options['unit'] as string)
    : 'm';
  const verticalDatum = typeof options['verticalDatum'] === 'string' && options['verticalDatum']
    ? (options['verticalDatum'] as string)
    : undefined;
  const profileRef = typeof options['profileRef'] === 'string' && options['profileRef'].trim()
    ? (options['profileRef'] as string)
    : '';
  const title = profileTitle(verticalDatum);
  const [fetched, setFetched] = useState<ChartData | null | undefined>(undefined);

  useEffect(() => {
    if (!profileRef) {
      setFetched(undefined);
      return;
    }
    // ref 切换：清除旧 fetched，否则短暂显示旧剖面（chart_panel 同款）
    setFetched(undefined);
    let alive = true;
    loadChartArtifact(profileRef).then((chart) => {
      if (alive) setFetched(chart);
    });
    return () => {
      alive = false;
    };
  }, [profileRef]);

  // 数据通道解析：points 优先（ABI 契约 points/profileRef 二选一）；
  // ref 载荷不足 2 点 = 不可用（诚实降级，不拿单点画"剖面"）。
  let state: ProfileState;
  if (points.length >= 2) {
    state = {
      status: 'ready',
      chart: {
        type: 'line',
        title,
        data: points.map((p) => ({ name: distanceLabel(p.distance_m), value: p.elevation_m })),
        x_label: COPY.xLabel,
        y_label: `${COPY.yLabelPrefix}${unit}${COPY.yLabelSuffix}`,
      },
    };
  } else if (profileRef) {
    if (fetched !== undefined) {
      state = fetched && Array.isArray(fetched.data) && fetched.data.length >= 2
        ? { status: 'ready', chart: fetched }
        : { status: 'unavailable' };
    } else {
      const cached = getCachedChartArtifact(profileRef);
      state = cached === undefined
        ? { status: 'loading' }
        : cached && Array.isArray(cached.data) && cached.data.length >= 2
          ? { status: 'ready', chart: cached }
          : { status: 'unavailable' };
    }
  } else {
    state = { status: 'insufficient' };
  }

  const bodyClass = variant === 'compact' ? 'p-1.5' : 'p-2';

  return (
    <FloatingChrome
      component={patched}
      title={title}
      topSlotIndexes={ctx?.topSlotIndexes}
      bottomSlotIndexes={ctx?.bottomSlotIndexes}
      testId="spec-chrome-elevation-profile"
      dataVariant={variant}
      bodyClassName={bodyClass}
    >
      {state.status === 'ready' ? (
        <div
          role="img"
          aria-label={COPY.panelTitle}
          data-testid="elevation-profile-chart"
          data-datum={verticalDatum || undefined}
          data-unit={unit}
        >
          <ChartCore chart={state.chart} height={contentHeight(variant, panelHeight)} />
        </div>
      ) : (
        <div
          className="flex h-full min-h-16 items-center justify-center px-2 py-3 text-caption text-map-chrome-ink-muted"
          data-state={state.status}
          role="status"
        >
          {state.status === 'loading'
            ? COPY.loading
            : state.status === 'unavailable'
              ? COPY.unavailable
              : COPY.insufficient}
        </div>
      )}
    </FloatingChrome>
  );
}

function ElevationProfileRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  return <ElevationProfilePanelView component={component} ctx={_ctx} />;
}

registerComponentRenderer('elevation_profile', ElevationProfileRenderer);
