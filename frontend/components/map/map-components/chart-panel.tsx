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
import {
  getViewportContext,
  getViewportGeneration,
  subscribeViewportContext,
} from '@/lib/selection/viewport-context';
import { registerChartRenderState, unregisterChartRenderState } from '@/lib/map-components/chart-render-registry';
import { commitComponentPatch } from '@/lib/mapspec/component-mutation';

/**
 * chart_panel 渲染器（D2）：MapSpec 图表面板。
 * - 数据通道：options.chart inline（adaptChartData 校验）或 options.chartRef
 *   （ref:chart-* → chart-artifact 模块按会话拉取，模块缓存 + 去重）；
 * - variant：default | compact（高 160 + 紧凑内边距）| transparent（去卡片
 *   底）| report（标题强调）；未知 variant 确定性回退 default；
 * - 失败/空数据渲染降级卡片（图表数据不可用 / 暂无图表数据），绝不崩 chrome。
 */

const CHART_PANEL_VARIANTS = new Set(['default', 'compact', 'transparent', 'report']);

// V4：kind 变体词表（chart_kinds native 子集；与 descriptor.variants 同表）。
// kind 变体是目录的 preset 句柄：variant 即图表类型预设（chart_switch_type
// Agent 命令也走 variant 通道）。
const CHART_KIND_VARIANTS = new Set([
  'bar', 'horizontal_bar', 'grouped_bar', 'stacked_bar',
  'line', 'area', 'scatter', 'histogram', 'box_plot',
  'pie', 'donut', 'radar', 'rose', 'timeseries', 'cumulative',
  'heat_matrix', 'kpi_card', 'ranking_list',
]);

/** kind 变体归属同 data-shape 家族才允许类型改写（防止 xy/矩阵数据被
 * 强转成 bar —— 形状不兼容时保持 payload 自带类型，诚实不硬转）。 */
const SERIES_SHAPE_KINDS = new Set([
  'bar', 'horizontal_bar', 'line', 'area', 'histogram',
  'pie', 'donut', 'timeseries', 'cumulative', 'rose',
]);

function resolvePanelVariant(component: MapSpecComponent): string {
  const variant = resolveVariant(component, 'default');
  return CHART_PANEL_VARIANTS.has(variant) ? variant : 'default';
}

function resolveKindPreset(component: MapSpecComponent): string | null {
  const variant = resolveVariant(component, 'default');
  return CHART_KIND_VARIANTS.has(variant) ? variant : null;
}

/** kind 预设应用：series-shape 家族内改写 type；其余形状保持原 type。 */
function applyKindPreset(chart: ChartData, kind: string | null): ChartData {
  if (!kind || chart.type === kind) return chart;
  if (!SERIES_SHAPE_KINDS.has(kind)) return chart;
  const seriesLike = chart.data.every(
    (p) => typeof p.name === 'string' && typeof p.value === 'number',
  );
  return seriesLike ? { ...chart, type: kind as ChartData['type'] } : chart;
}

/** 内容高度：compact 160；有显式面板高度时填满，否则与 chat 内嵌一致 200。 */
function contentHeight(variant: string, panelHeight: number | undefined): number | `${number}%` {
  if (typeof panelHeight === 'number' && panelHeight > 48) return '100%';
  return variant === 'compact' ? 160 : 200;
}

/* ── Workbench V4（Wave 5）：linked extent（视野联动过滤）──────────────────
 * options.extentLinked === true（显式 opt-in）时，图表数据点按绑定图层在
 * 当前视口内命中的要素属性（selectionField 值集）过滤展示。
 *
 * 无环设计：视口 → 图表是单向只读投影（viewport-context 是 transient
 * store，图表过滤不发布 selection、不改 MapSpec、不动视口）；图表自身的
 * 点击发布 selection（另一维度），selection 不驱动视口 —— 两个方向互不
 * 构成环。数据点没有绑定图层可过滤时如实展示全量（降级不伪装）。 */

/** 要素几何是否与 bbox 相交（有界采样：坐标序列取前 64 点近似）。导出仅供测试。 */
export function geometryIntersectsBbox(geometry: unknown, bbox: [number, number, number, number]): boolean {
  const [w, s, e, n] = bbox;
  const inBox = (x: number, y: number) => x >= w && x <= e && y >= s && y <= n;
  const coords = (c: unknown, depth = 0): number[] | null => {
    if (typeof c !== 'object' || c === null) return null;
    if (Array.isArray(c)) {
      if (typeof c[0] === 'number' && typeof c[1] === 'number') {
        return inBox(c[0], c[1]) ? [c[0], c[1]] : null;
      }
      if (depth > 3) return null;
      for (const item of c.slice(0, 64)) {
        const hit = coords(item, depth + 1);
        if (hit) return hit;
      }
    }
    return null;
  };
  const geom = geometry as { type?: string; coordinates?: unknown } | null;
  if (!geom?.coordinates) return false;
  return coords(geom.coordinates) !== null;
}

/** 视口内命中要素的 selectionField 值集（有界 ≤ 512）。导出仅供测试。 */
export function visibleCategoryValues(
  source: unknown,
  field: string,
  bbox: [number, number, number, number],
): Set<string> | null {
  const fc = source as { features?: Array<{ geometry?: unknown; properties?: Record<string, unknown> }> } | null;
  if (!fc || !Array.isArray(fc.features) || fc.features.length === 0) return null;
  const out = new Set<string>();
  const cap = Math.min(fc.features.length, 5000);
  for (let i = 0; i < cap; i += 1) {
    const f = fc.features[i];
    const value = f?.properties?.[field];
    if (value == null) continue;
    if (geometryIntersectsBbox(f?.geometry, bbox)) {
      out.add(String(value));
      if (out.size >= 512) break;
    }
  }
  return out;
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
  const kindPreset = resolveKindPreset(patched);
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

  // Workbench V4（Wave 5）：视野联动（显式 opt-in：options.extentLinked）。
  useSyncExternalStore(subscribeViewportContext, getViewportGeneration);
  const extentLinked = options['extentLinked'] === true;
  const extentFilterField = selectionField;

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

  // 视口过滤投影（Wave 5）：绑定图层已落地 GeoJSON 且视口 bbox 在场时，
  // 按 selectionField 值集过滤数据点；无法判定时展示全量（诚实降级）。
  // useMemo 键在视口代数上 —— 视口未变时重渲不重扫（有界扫描预算）。
  const boundRow = useHudStore((s) =>
    boundLayerId ? s.layers.find((row: { _mapspecLayerId?: string; id: string }) =>
      row.id === boundLayerId || row._mapspecLayerId === boundLayerId) : undefined);
  const extentFilteredChart = React.useMemo(() => {
    if (state.status !== 'ready' || !extentLinked || !extentFilterField || !boundRow) {
      return state.status === 'ready' ? state.chart : null;
    }
    const viewport = getViewportContext();
    if (!viewport?.bbox) return state.chart;
    const values = visibleCategoryValues(boundRow.source, extentFilterField, viewport.bbox);
    if (!values || values.size === 0) return state.chart;
    const filtered = state.chart.data.filter((p) => values.has(p.name));
    // 全不命中 = 视野内无该图层要素 —— 保留全量并降级（避免清空面板抖动）。
    return filtered.length > 0 ? { ...state.chart, data: filtered } : state.chart;
  }, [state, extentLinked, extentFilterField, boundRow]);

  const toggleExtentLinked = () => {
    void commitComponentPatch(patched.id, {
      options: { ...(options ?? {}), extentLinked: !extentLinked },
    }).catch(() => { /* 提交失败静默 —— 乐观面在 spec 回流时收敛 */ });
  };

  // V5 W5 rendered-state telemetry：把「面板是否真实渲染出带数据的
  // series」发布进观测注册表（RenderObservation.charts 消费，服务端
  // chart_required 数据级核验）。发布在渲染提交后一拍，零轮询。
  React.useEffect(() => {
    // review R2 #3：loading/取数中 = pending（非终态）—— 服务端对全 pending
    // 的观察按 warning 披露，绝不把取数竞速误判为「渲染了但无数据」error。
    const ready = state.status === 'ready' && !!extentFilteredChart
      && Array.isArray(extentFilteredChart.data) && extentFilteredChart.data.length > 0;
    registerChartRenderState(String(patched.id ?? ''), {
      rendered: ready,
      data_points: ready ? extentFilteredChart.data.length : 0,
      pending: state.status === 'loading',
    });
    return () => unregisterChartRenderState(String(patched.id ?? ''));
  }, [state.status, extentFilteredChart, patched.id]);

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
      {state.status === 'ready' && extentFilteredChart ? (
        <>
          <ChartCore
            chart={applyKindPreset(extentFilteredChart, kindPreset)}
            height={contentHeight(variant, panelHeight)}
            highlightedCategories={highlightedCategories}
            onSelectCategory={handleSelectCategory}
          />
          {boundLayerId && (
            <button
              type="button"
              aria-pressed={extentLinked}
              data-testid="chart-extent-linked"
              title="地图视野联动：图表只统计当前视野内的要素"
              onClick={toggleExtentLinked}
              className="mt-1 rounded-xs px-1 py-0.5 text-micro text-map-chrome-ink-muted transition-colors hover:bg-surface-hover hover:text-map-chrome-ink"
            >
              {extentLinked ? '◉ 视野联动' : '○ 视野联动'}
            </button>
          )}
        </>
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
