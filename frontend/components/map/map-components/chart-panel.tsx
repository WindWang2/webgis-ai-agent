'use client';
import React, { useEffect, useState } from 'react';
import type { ChartData } from '@/lib/types';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { adaptChartData } from '@/lib/chart-adapter';
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
 * chart_panel 渲染器（D2）：MapSpec 图表面板。
 * - 数据通道：options.chart inline（adaptChartData 校验）或 options.chartRef
 *   （ref:chart-* → chart-artifact 模块按会话拉取，模块缓存 + 去重）；
 * - variant：default | compact（高 160 + 紧凑内边距）| transparent（去卡片
 *   底）| report（标题强调）；未知 variant 确定性回退 default；
 * - 失败/空数据渲染降级卡片（图表数据不可用 / 暂无图表数据），绝不崩 chrome。
 */

const CHART_PANEL_VARIANTS = new Set(['default', 'compact', 'transparent', 'report']);

function resolvePanelVariant(component: MapSpecComponent): string {
  const variant = resolveVariant(component, 'default');
  return CHART_PANEL_VARIANTS.has(variant) ? variant : 'default';
}

/** 内容高度：compact 160；有显式面板高度时填满，否则与 chat 内嵌一致 200。 */
function contentHeight(variant: string, panelHeight: number | undefined): number | `${number}%` {
  if (typeof panelHeight === 'number' && panelHeight > 48) return '100%';
  return variant === 'compact' ? 160 : 200;
}

type ChartState =
  | { status: 'empty' }                    // 无 chart/chartRef
  | { status: 'loading' }                  // ref 拉取中
  | { status: 'unavailable' }              // 载荷非法 / 拉取失败
  | { status: 'ready'; chart: ChartData }; // 可渲染

/** inline 路径状态解析（chart 非法 → 不可用；两者皆无 → 空）。 */
function inlineChartState(component: MapSpecComponent): ChartState {
  const options = component.options ?? {};
  const inline = adaptChartData(options['chart']);
  if (inline) return { status: 'ready', chart: inline };
  if (options['chart'] === undefined) return { status: 'empty' };
  return { status: 'unavailable' };
}

/** ref 路径状态解析：本地 fetched 优先，其次模块缓存，未拉取 → loading。 */
function refChartState(chartRef: string, fetched: ChartData | null | undefined): ChartState {
  if (fetched !== undefined) {
    return fetched ? { status: 'ready', chart: fetched } : { status: 'unavailable' };
  }
  const cached = getCachedChartArtifact(chartRef);
  if (cached === undefined) return { status: 'loading' };
  return cached ? { status: 'ready', chart: cached } : { status: 'unavailable' };
}

function ChartPanelView({ component }: { component: MapSpecComponent }) {
  const patched = usePlacementPatchedComponent(component);
  const variant = resolvePanelVariant(patched);
  const placement = patched.placement;
  const panelHeight = placement?.mode === 'floating' ? placement.height : undefined;

  const options = patched.options ?? {};
  const chartRef = typeof options['chartRef'] === 'string' && options['chartRef'].trim()
    ? (options['chartRef'] as string)
    : '';
  const [fetched, setFetched] = useState<ChartData | null | undefined>(undefined);

  useEffect(() => {
    if (!chartRef) return;
    let alive = true;
    // 缓存命中（含其他面板已拉取）时 loadChartArtifact 兑现同一结果
    loadChartArtifact(chartRef).then((chart) => {
      if (alive) setFetched(chart);
    });
    return () => {
      alive = false;
    };
  }, [chartRef]);

  const state: ChartState = chartRef ? refChartState(chartRef, fetched) : inlineChartState(patched);

  const title = typeof options['title'] === 'string' && options['title'].trim()
    ? (options['title'] as string)
    : state.status === 'ready'
      ? state.chart.title
      : '图表';

  const bodyClass = variant === 'compact' ? 'p-1.5' : variant === 'report' ? 'p-3' : 'p-2';

  return (
    <FloatingChrome
      component={patched}
      title={title}
      testId="spec-chrome-chart-panel"
      dataVariant={variant}
      transparent={variant === 'transparent'}
      bodyClassName={bodyClass}
    >
      {state.status === 'ready' ? (
        <ChartCore chart={state.chart} height={contentHeight(variant, panelHeight)} />
      ) : (
        <div
          className="flex h-full min-h-16 items-center justify-center px-2 py-3 text-caption text-map-chrome-ink-muted"
          data-state={state.status}
          role="status"
        >
          {state.status === 'loading'
            ? '图表加载中…'
            : state.status === 'unavailable'
              ? '图表数据不可用'
              : '暂无图表数据'}
        </div>
      )}
    </FloatingChrome>
  );
}

function ChartPanelRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  return <ChartPanelView component={component} />;
}

registerComponentRenderer('chart_panel', ChartPanelRenderer);
