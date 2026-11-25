'use client';

/**
 * G46 联合探索图表组件（MapSpec chrome 组件；cardinality=multiple）。
 *
 * 每实例 = 一个条件控件（chartId）：分类条形图点击类别 → 发布/切换
 * typed 值条件；数值区间直方图点击桶 → 发布区间条件。facet 口径渲染
 * （排除自身条件 —— 显示"其它条件下的可选项"），匹配总数仍由全条件
 * 口径的 preview.match_count 承载（两套口径在回执/UI 分开标注）。
 *
 * 与旧 chart_panel 的 transient 单选互不影响（E34）：本组件只写
 * condition-store（活动过滤），不发 publishSelection。
 */
import React, { useMemo } from 'react';
import { useSyncExternalStore } from 'react';
import { useT } from '@/lib/i18n/useT';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { usePlacementPatchedComponent } from './floating-chrome';
import type { RendererContext } from './types';
import {
  getChartCondition,
  getExplorationGeneration,
  setCondition,
  subscribeExploration,
} from '@/lib/linked-exploration/condition-store';
import {
  getChartPreview,
  getPreviewGeneration,
  subscribePreview,
} from '@/lib/linked-exploration/preview-store';
import type { ChartStatistics } from '@/lib/linked-exploration/contracts';

const BAR_MAX = 18;   // 类别条形渲染上限（完整统计在面板/回执）
const BIN_MAX = 32;

export function ExplorationChartPanelRenderer(
  component: MapSpecComponent,
  ctx?: RendererContext,
): React.ReactNode {
  void ctx;
  const t = useT('linkedExploration');
  const patched = usePlacementPatchedComponent(component);
  useSyncExternalStore(subscribeExploration, getExplorationGeneration);
  useSyncExternalStore(subscribePreview, getPreviewGeneration);

  const options = patched.options ?? {};
  const chartId = typeof options['chartId'] === 'string' ? options['chartId'] as string : '';
  const kind = options['kind'] === 'range' ? 'range' : 'category';
  const field = typeof options['field'] === 'string' ? options['field'] as string : '';
  const title = typeof options['title'] === 'string' && options['title']
    ? options['title'] as string
    : `${field} · ${kind === 'category' ? t('panel.kindCategory') : t('panel.kindRange')}`;
  const preview = useMemo(
    // eslint-disable-next-line react-hooks/exhaustive-deps
    () => getChartPreview(chartId), [chartId, getPreviewGeneration()]);
  const condition = useMemo(
    () => getChartCondition(chartId),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chartId, getExplorationGeneration()],
  );

  if (!chartId || !field) return null;

  return (
    <div
      className="rounded-lg border bg-panel p-3 text-sm shadow"
      data-testid={`g46-chart-component-${chartId}`}
      role="group"
      aria-label={title}
    >
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-medium">{title}</h3>
        <span className="rounded bg-surface px-1 text-xs opacity-70">
          {t('chart.facetScope')}
        </span>
      </div>
      {!preview ? (
        <p className="text-xs opacity-60" data-testid={`g46-chart-${chartId}-no-data`}>
          {t('chart.noData')}
        </p>
      ) : (
        <ChartBody
          chartId={chartId}
          kind={kind}
          field={field}
          stats={preview.facet.statistics}
          activeCondition={condition as { values?: unknown[]; includeNull?: boolean; gte?: number; gt?: number; lte?: number; lt?: number } | null}
          t={t}
        />
      )}
      {preview?.facet.statistics.truncated && (
        <p className="mt-1 text-xs opacity-70">{t('chart.truncatedNote')}</p>
      )}
    </div>
  );
}

function ChartBody({
  chartId, kind, field, stats, activeCondition, t,
}: {
  chartId: string;
  kind: 'category' | 'range';
  field: string;
  stats: ChartStatistics;
  activeCondition: { values?: unknown[]; includeNull?: boolean; gte?: number; gt?: number; lte?: number; lt?: number } | null;
  t: (k: string, vars?: Record<string, string>) => string;
}) {
  const hidden = kind === 'category'
    ? Math.max(0, (stats.rows?.length ?? 0) - BAR_MAX)
    : Math.max(0, (stats.bins?.length ?? 0) - BIN_MAX);
  if (kind === 'category') {
    return (
      <>
        <CategoryBars
          chartId={chartId}
          field={field}
          rows={(stats.rows ?? []).slice(0, BAR_MAX)}
          active={activeCondition}
          t={t}
        />
        {hidden > 0 && (
          // 客户端截断如实披露剩余条数（服务端 truncated 提示不覆盖该层）。
          <p className="mt-1 text-xs opacity-70" data-testid={`g46-chart-${chartId}-client-truncated`}>
            …+{hidden}
          </p>
        )}
      </>
    );
  }
  return (
    <>
      <RangeHistogram
        chartId={chartId}
        field={field}
        bins={(stats.bins ?? []).slice(0, BIN_MAX)}
        active={activeCondition}
      />
      {hidden > 0 && (
        <p className="mt-1 text-xs opacity-70" data-testid={`g46-chart-${chartId}-client-truncated`}>
          …+{hidden}
        </p>
      )}
    </>
  );
}

function CategoryBars({
  chartId, field, rows, active, t,
}: {
  chartId: string;
  field: string;
  rows: Array<{
    label: string;
    count: number;
    machine_value: string | number | boolean | null;
    is_null: boolean;
    is_other: boolean;
    other_filterable?: boolean;
    other_values?: Array<string | number | boolean | null>;
  }>;
  active: { values?: unknown[]; includeNull?: boolean } | null;
  t: (k: string, vars?: Record<string, string>) => string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  const activeValues = new Set((active?.values ?? []).map((v) => `${typeof v}:${String(v)}`));
  const nullActive = active?.includeNull === true;

  const toggle = (row: typeof rows[number]) => {
    if (row.is_other && row.other_filterable === false) return; // 超预算禁筛（E13）
    let values: Array<string | number | boolean | null>;
    let includeNull = nullActive;
    if (row.is_other) {
      values = [...((row.other_values ?? []) as Array<string | number | boolean | null>)];
    } else if (row.is_null) {
      values = [...((active?.values ?? []) as Array<string | number | boolean | null>)];
      includeNull = !nullActive;
    } else {
      const cur: Array<string | number | boolean | null> = [...((active?.values ?? []) as Array<string | number | boolean | null>)];
      const key = `${typeof row.machine_value}:${String(row.machine_value)}`;
      const idx = cur.findIndex((v) => `${typeof v}:${String(v)}` === key);
      if (idx >= 0) cur.splice(idx, 1);
      else cur.push(row.machine_value);
      values = cur;
    }
    // 空分类条件由 store 自动移除（toggle-off 最后一个值）
    setCondition({
      kind: 'category', conditionId: `cond-${chartId}`, chartId, field,
      values, includeNull,
    });
  };

  return (
    <ul className="space-y-1" data-testid={`g46-bars-${chartId}`}>
      {rows.map((row, i) => {
        const activeRow = row.is_null
          ? nullActive
          : !row.is_other && activeValues.has(`${typeof row.machine_value}:${String(row.machine_value)}`);
        const disabled = row.is_other && row.other_filterable === false;
        return (
          <li key={`${row.label}-${i}`}>
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-surface disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => toggle(row)}
              disabled={disabled}
              aria-pressed={activeRow}
              data-testid={`g46-bar-${chartId}-${i}`}
              title={disabled ? t('chart.otherNotFilterable') : row.label}
            >
              <span className={`h-2 flex-shrink-0 rounded-sm ${activeRow ? 'bg-accent' : 'bg-primary'}`}
                style={{ width: `${Math.max(4, (row.count / max) * 120)}px` }} />
              <span className="w-24 flex-shrink-0 truncate text-xs" title={row.label}>{row.label}</span>
              <span className="text-xs opacity-70">{row.count}</span>
              {row.is_other && row.other_filterable === false && (
                <span className="text-xs opacity-50">{t('chart.otherOverflow')}</span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function RangeHistogram({
  chartId, field, bins, active,
}: {
  chartId: string;
  field: string;
  bins: Array<{ index: number; gte: number; lte: number; upper_exclusive: boolean; label: string; count: number }>;
  active: { gte?: number; gt?: number; lte?: number; lt?: number } | null;
}) {
  const max = Math.max(1, ...bins.map((b) => b.count));
  const activeBin = (b: typeof bins[number]) => {
    if (!active) return false;
    const lo = active.gte ?? active.gt;
    const hi = active.lte ?? active.lt;
    if (lo === undefined && hi === undefined) return false;
    // 近似高亮：桶与活动区间相交
    const loOk = lo === undefined || b.lte >= lo;
    const hiOk = hi === undefined || b.gte <= hi;
    return loOk && hiOk;
  };

  const pick = (b: typeof bins[number]) => {
    // 点击桶 → 条件切到该桶精确范围 [gte, lte]（末桶闭语义由后端
    // compile 的 gte/lte 闭区间表达；upper_exclusive 桶用 gte + lt）
    setCondition({
      kind: 'range', conditionId: `cond-${chartId}`, chartId, field,
      ...(b.upper_exclusive
        ? { gte: b.gte, lt: b.lte }
        : { gte: b.gte, lte: b.lte }),
    });
  };

  return (
    <div className="flex items-end gap-1" data-testid={`g46-hist-${chartId}`}>
      {bins.map((b) => (
        <button
          key={b.index}
          type="button"
          className="group flex flex-col items-center"
          onClick={() => pick(b)}
          aria-pressed={activeBin(b)}
          title={`${b.label} · ${b.count}`}
          data-testid={`g46-bin-${chartId}-${b.index}`}
        >
          <span className="text-[10px] opacity-0 group-hover:opacity-70">{b.count}</span>
          <span
            className={`w-5 rounded-t-sm ${activeBin(b) ? 'bg-accent' : 'bg-primary'}`}
            style={{ height: `${Math.max(3, (b.count / max) * 80)}px` }}
          />
        </button>
      ))}
    </div>
  );
}

registerComponentRenderer('exploration_chart_panel', ExplorationChartPanelRenderer);
