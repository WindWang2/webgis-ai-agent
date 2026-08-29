'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { resolveVariant } from './helpers';
import { FloatingChrome, usePlacementPatchedComponent } from './floating-chrome';
import type { RendererContext } from './types';

/**
 * statistics_panel 渲染器（D2）：统计摘要面板。
 * options.stats = {title?, items:[{label, value, unit?, emphasis?}]}；
 * 防御式校验（坏载荷 → 空态卡片，不崩 chrome）；variant default | compact。
 */

interface StatItem {
  label: string;
  value: string | number;
  unit?: string;
  emphasis?: boolean;
}

interface StatsPayload {
  title?: string;
  items: StatItem[];
}

/** 防御式解析：坏条目剔除，整体坏 → null（空态）。 */
function parseStats(raw: unknown): StatsPayload | null {
  if (!raw || typeof raw !== 'object') return null;
  const stats = raw as Record<string, unknown>;
  const itemsRaw = stats['items'];
  if (!Array.isArray(itemsRaw) || itemsRaw.length === 0) return null;
  const items: StatItem[] = [];
  for (const item of itemsRaw) {
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    const label = rec['label'];
    const value = rec['value'];
    if (typeof label !== 'string' || !label.trim()) continue;
    if (typeof value !== 'number' && typeof value !== 'string') continue;
    const unit = typeof rec['unit'] === 'string' ? (rec['unit'] as string) : undefined;
    const emphasis = rec['emphasis'] === true;
    items.push({ label, value, unit, emphasis });
  }
  if (items.length === 0) return null;
  const title = typeof stats['title'] === 'string' ? (stats['title'] as string) : undefined;
  return { title, items };
}

function StatisticsPanelView({ component, ctx }: { component: MapSpecComponent; ctx?: RendererContext }) {
  const patched = usePlacementPatchedComponent(component);
  const variant = resolveVariant(patched, 'default') === 'compact' ? 'compact' : 'default';
  const stats = parseStats(patched.options?.['stats']);
  const title = stats?.title || '统计摘要';

  return (
    <FloatingChrome
      component={patched}
      title={title}
      topSlotIndexes={ctx?.topSlotIndexes}
      testId="spec-chrome-statistics-panel"
      dataVariant={variant}
      bodyClassName={variant === 'compact' ? 'p-1.5' : 'p-2'}
    >
      {stats ? (
        <dl className={`flex flex-col ${variant === 'compact' ? 'gap-0.5' : 'gap-1'}`}>
          {stats.items.map((item, i) => (
            <div
              key={`${item.label}#${i}`}
              data-emphasis={item.emphasis ? 'true' : undefined}
              className={`flex items-baseline justify-between gap-3 ${variant === 'compact' ? 'px-1 py-0.5' : 'px-1 py-1'} ${
                item.emphasis
                  ? 'rounded-sm border-l-2 border-map-chrome-ink bg-[color:var(--surface-sunken)] font-semibold text-map-chrome-ink'
                  : 'text-map-chrome-ink-muted'
              }`}
            >
              <dt className="min-w-0 truncate text-caption">{item.label}</dt>
              <dd className="flex shrink-0 items-baseline gap-1 tabular-nums">
                <span className={variant === 'compact' ? 'text-caption font-medium text-map-chrome-ink' : 'text-body font-medium text-map-chrome-ink'}>
                  {item.value}
                </span>
                {item.unit ? (
                  <span className="text-micro text-map-chrome-ink-muted">{item.unit}</span>
                ) : null}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <div
          className="flex min-h-12 items-center justify-center px-2 py-3 text-caption text-map-chrome-ink-muted"
          data-state="empty"
          role="status"
        >
          暂无统计数据
        </div>
      )}
    </FloatingChrome>
  );
}

function StatisticsPanelRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  return <StatisticsPanelView component={component} ctx={_ctx} />;
}

registerComponentRenderer('statistics_panel', StatisticsPanelRenderer);
