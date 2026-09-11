'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { positionClass, resolveVariant, stackedBottomStyle } from './helpers';
import type { RendererContext } from './types';
import type { LegendSpec } from '@/lib/map-kit/types';
import { deriveLegendModel } from '@/lib/map-kit/legend-model';
import { useT } from '@/lib/i18n/useT';

function legendForComponent(component: MapSpecComponent, spec: RendererContext['spec']): LegendSpec | undefined {
  const layerId = (component as unknown as { options?: Record<string, unknown> }).options?.['layerId'];
  if (typeof layerId === 'string' && layerId && spec) {
    const layer = spec.layers.find((l) => l.id === layerId) as unknown as { legend_spec?: LegendSpec } | undefined;
    if (layer?.legend_spec) return layer.legend_spec;
  }
  // V4：legend 类型自动发现扩展 —— bivariate/continuous 色阵与连续色带
  // 也参与兜底发现（显式 layerId 绑定仍优先）。
  // 注：'continuous' 不进发现词表 —— legendEntries 无 continuous 分支
  //（连续色带由 colorbar 组件承接，图例卡兜底会渲染空卡）
  const wanted: Record<string, string[]> = {
    legend: ['graduated', 'bivariate'],
    categorical_legend: ['categorical'],
  };
  const types = wanted[component.type] ?? [];
  const found = spec?.layers.find((l) => {
    const ls = (l as unknown as { legend_spec?: { type?: string } }).legend_spec;
    return ls != null && (types.length === 0 || types.includes(String(ls.type ?? '')));
  }) as unknown as { legend_spec?: LegendSpec } | undefined;
  return found?.legend_spec;
}

// W5（ADR-0120）：条目推导收敛至 legend-model 单源 —— 本函数保持签名作为
// live 呈现层薄壳（slice(0,8) 呈现语义仍在调用点）。行为 delta（收敛表）：
// categorical 无色条目由"丢弃"改为 #888 兜底（不静默丢数据）；
// graduated cap 由 min() 改为 min(max(,0),) 防负数。
// 注：continuous/bivariate 无通用条目分支（专用组件承接，模型 kind 分派）。
export function legendEntries(legend: LegendSpec | undefined): { color: string; label: string }[] {
  const model = deriveLegendModel(legend);
  if (!model) return [];
  if (legend?.type === 'bivariate' || legend?.type === 'continuous' || legend?.type === 'divergent') {
    // 专用渲染器语义：通用条目卡不消费这些 kind（保留原 [] 行为）
    return [];
  }
  return model.entries.map((e) => ({ color: e.color, label: e.label }));
}

// D7：legend 族 variant —— compact（紧凑内边距/行距）| academic（缺省现状）
// | report（卡片 + 标题强调条）。未知 variant 确定性回退 academic。
function legendVariantClasses(variant: string): { root: string; title: string } {
  if (variant === 'compact') {
    return { root: 'px-1.5 py-1', title: 'text-micro font-medium' };
  }
  if (variant === 'report') {
    return { root: 'px-3 py-2', title: 'border-b border-map-chrome-border pb-1 text-caption font-semibold' };
  }
  return { root: 'px-2 py-1.5', title: 'text-micro font-medium' };
}

function BivariateMatrix({ legend, title }: { legend: LegendSpec; title?: string }) {
  // V4：3×3 双变量色阵（行=变量 B，列=变量 A；颜色与 paint match 逐格同源）
  const colors = (legend as unknown as { colors?: string[] }).colors ?? [];
  const n = Math.min(4, Math.max(2, Number((legend as unknown as { n?: number }).n ?? 3)));
  const labelA = String((legend as unknown as { label_a?: string }).label_a ?? '');
  const labelB = String((legend as unknown as { label_b?: string }).label_b ?? '');
  if (colors.length < n * n) return null;
  return (
    <div data-testid="spec-chrome-bivariate-legend" className="map-chrome absolute z-30 rounded-chrome px-2 py-1.5 bottom-8 left-2" data-component-anchor>
      {title && <div className="text-micro font-medium text-map-chrome-ink">{title}</div>}
      <div className="mt-1 flex flex-col gap-0.5">
        <div className="flex items-center gap-1">
          <span className="w-4 text-right text-micro text-map-chrome-ink-muted" aria-hidden>↑{labelB.slice(0, 4)}</span>
          <div className="grid gap-px" style={{ gridTemplateColumns: `repeat(${n}, 14px)` }}>
            {Array.from({ length: n * n }, (_, i) => (
              <span key={i} aria-hidden className="h-3.5 w-3.5" style={{ background: colors[i] }} />
            ))}
          </div>
        </div>
        <div className="flex items-center gap-1 text-micro text-map-chrome-ink-muted">
          <span className="w-4" aria-hidden />
          <span>→{labelA.slice(0, 10)}</span>
        </div>
      </div>
    </div>
  );
}

function LegendRenderer(component: MapSpecComponent, ctx: RendererContext) {
const t = useT();
  const legend = legendForComponent(component, ctx.spec);
  if (!legend) return null;
  const variant = resolveVariant(component, 'academic');
  // V4：composite 变体显式要求多图层复合（优先于单图层类型分派）
  if (variant === 'composite') {
    return renderComposite(component, ctx, variant);
  }
  // V4：双变量色阵图例 —— legend.type === 'bivariate'（bivariate_choropleth
  // / bivariate_raster 的 legend_spec 同源投影）
  if (legend.type === 'bivariate') {
    return <BivariateMatrix legend={legend} title={(legend as unknown as { title?: string }).title} />;
  }
  const entries = legendEntries(legend);
  if (!entries.length) return null;
  const classes = legendVariantClasses(variant);
  // V4：size 变体 —— 比例符号尺寸图例（半径 ∝ sqrt(value) 契约的可视化）
  if (variant === 'size') {
    const sizes = [6, 10, 15];
    return (
      <div data-testid="spec-chrome-legend" data-variant={variant} style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 rounded-chrome ${classes.root} ${positionClass(component)}`} aria-label={t('map.legends.sizeAria')}>
        <div className="text-micro font-medium text-map-chrome-ink">{t('map.legends.sizeTitle')}</div>
        <div className="mt-1 flex items-end gap-2">
          {sizes.map((r, i) => (
            <span key={i} aria-hidden className="rounded-full border border-map-chrome-border bg-map-chrome-ink/20" style={{ width: r * 2, height: r * 2 }} />
          ))}
        </div>
      </div>
    );
  }
  // V4：line 变体 —— 线宽分级图例（graduated_line/network_flow 同契约）
  if (variant === 'line') {
    return (
      <div data-testid="spec-chrome-legend" data-variant={variant} style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 rounded-chrome ${classes.root} ${positionClass(component)}`} aria-label={t('map.legends.widthAria')}>
        <div className="text-micro font-medium text-map-chrome-ink">{t('map.legends.widthTitle')}</div>
        <div className="mt-1 flex flex-col gap-1">
          {[1, 2.5, 4.5].map((w, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <span aria-hidden className="inline-block w-5 rounded-sm bg-map-chrome-ink" style={{ height: w }} />
              <span className="text-micro tabular-nums text-map-chrome-ink-muted">{entries[i]?.label ?? ''}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }
  const compact = variant === 'compact';
  // V3（ADR-0101 D3）：horizontal —— 图例项横向排布换行（窄图幅横向空间
  // 充裕时），其余变体保持纵向。
  const layoutClass = variant === 'horizontal'
    ? `flex flex-row flex-wrap ${compact ? 'mt-0.5 gap-x-2 gap-y-0.5' : 'mt-1 gap-x-3 gap-y-1'}`
    : `flex flex-col ${compact ? 'mt-0.5 gap-0.5' : 'mt-1 gap-1'}`;
  // V4：uncertainty 变体 —— 分级条目按透明度递减渲染（与
  // UNCERTAINTY_OPACITY 的 fill-opacity 反向插值契约一致：越透明越不确定）
  const opacityFor = (idx: number) => variant === 'uncertainty' ? 0.25 + (0.6 * idx) / Math.max(1, entries.length - 1) : 1;
  return (
    <div data-testid="spec-chrome-legend" data-variant={variant} style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 rounded-chrome ${classes.root} ${positionClass(component)}`} aria-label={`分级图例${variant === 'horizontal' ? '（横向）' : ''}${variant === 'uncertainty' ? '（透明度=不确定性）' : ''}`}>
      {(legend as unknown as { title?: string }).title && <div className={`text-map-chrome-ink ${classes.title}`}>{(legend as unknown as { title: string }).title}</div>}
      <div className={layoutClass}>
        {entries.slice(0, 8).map((e, j) => (
          <div key={j} className="flex items-center gap-1.5">
            <span aria-hidden className="h-2.5 w-4 rounded-sm" style={{ background: e.color, opacity: opacityFor(j) }} />
            <span className="text-micro tabular-nums text-map-chrome-ink-muted">{e.label}</span>
          </div>
        ))}
        {entries.length > 8 && (
          // W7：溢出指示（导出件为全集 —— 差异由导出侧 legend_entries_truncated
          // 诊断披露，live 侧如实告知还有 N 条未示）。
          <div className="text-micro text-map-chrome-ink-muted" aria-label={`还有 ${entries.length - 8} 条图例未显示`}>…+{entries.length - 8}</div>
        )}
      </div>
      {variant === 'uncertainty' && (
        <div className="mt-1 border-t border-map-chrome-border pt-0.5 text-micro text-map-chrome-ink-muted">{t('map.legends.opacityHint')}</div>
      )}
    </div>
  );
}

// V4：composite 变体 —— 多层复合图例（所有携带 legend_spec 的图层分组）
function renderComposite(component: MapSpecComponent, ctx: RendererContext, variant: string) {
const t = useT();
  const groups = (ctx.spec?.layers ?? [])
    .map((l) => l as unknown as { id: string; legend_spec?: LegendSpec & { title?: string } })
    .filter((l) => l.legend_spec != null)
    .slice(0, 3);
  return (
    <div data-testid="spec-chrome-legend" data-variant={variant} style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 rounded-chrome px-2 py-1.5 ${positionClass(component)}`} aria-label={t('map.legends.compositeAria')}>
      {groups.map((g) => {
        const gEntries = legendEntries(g.legend_spec).slice(0, 6);
        if (!gEntries.length) return null;
        return (
          <div key={g.id} className="mb-1 last:mb-0">
            <div className="text-micro font-medium text-map-chrome-ink">{g.legend_spec?.title || g.id}</div>
            <div className="mt-0.5 flex flex-col gap-0.5">
              {gEntries.map((e, j) => (
                <div key={j} className="flex items-center gap-1.5">
                  <span aria-hidden className="h-2.5 w-4 rounded-sm" style={{ background: e.color }} />
                  <span className="text-micro tabular-nums text-map-chrome-ink-muted">{e.label}</span>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function CategoricalLegendRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const legend = legendForComponent(component, ctx.spec);
  if (!legend || legend.type !== 'categorical') return null;
  const entries = legendEntries(legend);
  if (!entries.length) return null;
  const variant = resolveVariant(component, 'academic');
  const classes = legendVariantClasses(variant);
  const compact = variant === 'compact';
  const layoutClass = variant === 'horizontal'
    ? `flex flex-row flex-wrap ${compact ? 'mt-0.5 gap-x-2 gap-y-0.5' : 'mt-1 gap-x-3 gap-y-1'}`
    : `flex flex-col ${compact ? 'mt-0.5 gap-0.5' : 'mt-1 gap-1'}`;
  return (
    <div data-testid="spec-chrome-categorical-legend" data-variant={variant} style={stackedBottomStyle(component, ctx.bottomSlotIndexes)} className={`map-chrome absolute z-30 rounded-chrome ${classes.root} ${positionClass(component)}`} aria-label={`分类图例${variant === 'horizontal' ? '（横向）' : ''}`}>
      {(legend as unknown as { title?: string }).title && <div className={`text-map-chrome-ink ${classes.title}`}>{(legend as unknown as { title: string }).title}</div>}
      <div className={layoutClass}>
        {entries.slice(0, 8).map((e, j) => (
          <div key={j} className="flex items-center gap-1.5">
            <span aria-hidden className="h-2.5 w-4 rounded-sm" style={{ background: e.color }} />
            <span className="text-micro text-map-chrome-ink-muted">{e.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

registerComponentRenderer('legend', LegendRenderer);
registerComponentRenderer('categorical_legend', CategoricalLegendRenderer);
