'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { resolveVariant } from './helpers';
import { FloatingChrome, usePlacementPatchedComponent } from './floating-chrome';
import type { RendererContext } from './types';

/**
 * uncertainty_panel 渲染器（VNext §5）：options.uncertainty =
 * {items: [{label, kind, detail?}], sampleNote?}。插值不确定性/样本
 * 限制/区间披露 —— 诚实呈现「这个结果有多可信」。防御式解析。
 */

interface UncertaintyItem {
  label: string;
  kind: string;
  detail?: string;
}

interface UncertaintyPayload {
  items?: UncertaintyItem[];
  sampleNote?: string;
}

function parseUncertainty(raw: unknown): UncertaintyPayload | null {
  if (!raw || typeof raw !== 'object') return null;
  const rec = raw as Record<string, unknown>;
  const payload: UncertaintyPayload = {};
  const itemsRaw = rec['items'];
  if (Array.isArray(itemsRaw) && itemsRaw.length > 0) {
    const items: UncertaintyItem[] = [];
    for (const item of itemsRaw) {
      if (!item || typeof item !== 'object') continue;
      const r = item as Record<string, unknown>;
      if (typeof r['label'] !== 'string' || !r['label'].trim()) continue;
      items.push({
        label: r['label'],
        kind: typeof r['kind'] === 'string' ? (r['kind'] as string) : 'interval',
        detail: typeof r['detail'] === 'string' ? (r['detail'] as string) : undefined,
      });
    }
    if (items.length > 0) payload.items = items;
  }
  if (typeof rec['sampleNote'] === 'string' && rec['sampleNote'].trim()) {
    payload.sampleNote = rec['sampleNote'] as string;
  }
  return payload.items || payload.sampleNote ? payload : null;
}

const KIND_LABELS: Record<string, string> = {
  interval: '区间',
  variance: '方差',
  confidence: '置信度',
  sample: '样本',
  model: '模型',
};

function UncertaintyPanelView({ component, ctx }: { component: MapSpecComponent; ctx?: RendererContext }) {
  const patched = usePlacementPatchedComponent(component);
  const variant = resolveVariant(patched, 'default') === 'compact' ? 'compact' : 'default';
  const uncertainty = parseUncertainty(patched.options?.['uncertainty']);

  return (
    <FloatingChrome
      component={patched}
      title="不确定性"
      topSlotIndexes={ctx?.topSlotIndexes}
      bottomSlotIndexes={ctx?.bottomSlotIndexes}
      testId="spec-chrome-uncertainty-panel"
      dataVariant={variant}
      bodyClassName={variant === 'compact' ? 'p-1.5' : 'p-2'}
    >
      {uncertainty ? (
        <div role="list" className={`flex flex-col ${variant === 'compact' ? 'gap-0.5' : 'gap-1'}`}>
          {uncertainty.items?.map((item, i) => (
            <div
              key={`${item.label}#${i}`}
              role="listitem"
              className="flex items-baseline justify-between gap-2 px-1 py-0.5 text-map-chrome-ink"
            >
              <span className="min-w-0 truncate text-caption">
                <span className="mr-1 text-micro text-map-chrome-ink-muted">
                  {KIND_LABELS[item.kind] ?? item.kind}
                </span>
                {item.label}
              </span>
              {item.detail ? (
                <span className="shrink-0 tabular-nums text-caption text-map-chrome-ink-muted">
                  {item.detail}
                </span>
              ) : null}
            </div>
          ))}
          {uncertainty.sampleNote ? (
            <div className="border-t border-map-chrome-line px-1 pt-1 text-micro text-map-chrome-ink-muted">
              {uncertainty.sampleNote}
            </div>
          ) : null}
        </div>
      ) : (
        <div
          className="flex min-h-12 items-center justify-center px-2 py-3 text-caption text-map-chrome-ink-muted"
          data-state="empty"
          role="status"
        >
          无不确定性披露
        </div>
      )}
    </FloatingChrome>
  );
}

function UncertaintyPanelRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  return <UncertaintyPanelView component={component} ctx={_ctx} />;
}

registerComponentRenderer('uncertainty_panel', UncertaintyPanelRenderer);
