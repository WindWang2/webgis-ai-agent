'use client';
import React, { useEffect, useState, useSyncExternalStore } from 'react';
import type { ChartData } from '@/lib/types';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { useHudStore } from '@/lib/store/useHudStore';
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
import {
  clearSelection,
  getSelection,
  getSelectionGeneration,
  publishSelection,
  subscribeSelection,
} from '@/lib/selection/selection-store';

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

function ChartPanelView({ component, ctx }: { component: MapSpecComponent; ctx?: RendererContext }) {
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
    if (!chartRef) {
      setFetched(undefined);
      return;
    }
    // ref 切换：清除旧 fetched，否则短暂显示旧图
    setFetched(undefined);
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

  // Workspace V2（Goal D）：map ↔ chart 共享选择。
  // - chart→map（D3）：类别点击发布 select（layer_id + filter_field 协议），
  //   地图侧编译为要素过滤（仅过滤，不重查/不重建 —— live-spec 复用通道）；
  //   filter_field 缺席（组件未声明 selectionField）→ 仅状态高亮（D4 降级）；
  // - map→chart（D4）：selection.source=map 且 layer 匹配时，按本面板的
  //   selectionField 从有界属性快照推导高亮类别；
  // - 选择是 transient UI 状态：不写 MapSpec、不产生 mutation（见
  //   selection-store 契约）。
  useSyncExternalStore(subscribeSelection, getSelectionGeneration);
  const selection = getSelection();
  const selectionField = typeof options['selectionField'] === 'string'
    ? (options['selectionField'] as string)
    : '';
  const boundLayerId = typeof options['layerId'] === 'string' ? (options['layerId'] as string) : '';

  // 面板卸载（隐藏 enabled:false / spec 移除 / dock 换页）时，清掉本面板
  // 发布的 chart 选择 —— 否则一张不可见图表面板的过滤会持续作用于地图
  // （无主的 stale filter）。只清自己 layer 上的 chart 选择（map/table
  // 来源的选择不受影响）。
  useEffect(() => {
    return () => {
      const sel = getSelection()
      if (sel && sel.source === 'chart' && sel.layer_id === boundLayerId) {
        clearSelection()
      }
    }
  }, [boundLayerId])
  // id 空间桥接（GIS review F18）：chart 绑定 spec 层 id，map 选择发布
  // HUD 行 id —— 两空间可能不同（_mapspecLayerId 别名）。命中任一即视为
  // 同一图层（别名缺席时如实只比原生键）。
  const selectionMatchesLayer =
    !!selection
    && !!boundLayerId
    && (selection.layer_id === boundLayerId
      || useHudStore
        .getState()
        .layers.some((row: { _mapspecLayerId?: string; id: string }) =>
          row._mapspecLayerId === boundLayerId && row.id === selection?.layer_id));
  const highlightedCategories =
    selection
    && selection.source === 'map'
    && selectionMatchesLayer
      && selectionField
      && selection.properties
      && selection.properties[selectionField] != null
        ? [String(selection.properties[selectionField])]
        : undefined;
  const handleSelectCategory =
    boundLayerId && state.status === 'ready'
      ? (name: string) => {
          const currentCategories = getSelection()?.selected_categories ?? [];
          const toggleOff =
            getSelection()?.source === 'chart' && currentCategories.length === 1
            && currentCategories[0] === name;
          if (toggleOff) {
            publishSelection('clear_selection', { source: 'chart', layer_id: boundLayerId });
            return;
          }
          publishSelection('select', {
            source: 'chart',
            layer_id: boundLayerId,
            selected_categories: [name],
            filter_field: selectionField || undefined,
            artifact_ref: chartRef || undefined,
          });
        }
      : null;

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
      topSlotIndexes={ctx?.topSlotIndexes}
      testId="spec-chrome-chart-panel"
      dataVariant={variant}
      transparent={variant === 'transparent'}
      bodyClassName={bodyClass}
    >
      {state.status === 'ready' ? (
        <ChartCore
          chart={state.chart}
          height={contentHeight(variant, panelHeight)}
          highlightedCategories={highlightedCategories}
          onSelectCategory={handleSelectCategory}
        />
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
  return <ChartPanelView component={component} ctx={_ctx} />;
}

registerComponentRenderer('chart_panel', ChartPanelRenderer);
