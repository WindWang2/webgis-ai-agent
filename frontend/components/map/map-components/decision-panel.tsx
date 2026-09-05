'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { resolveVariant } from './helpers';
import { FloatingChrome, usePlacementPatchedComponent } from './floating-chrome';
import type { RendererContext } from './types';

/**
 * decision_panel 渲染器（VNext §12）：options.decision =
 * {method?, rows: [{rank, name, score?, basis?}], weightSource?, vetoes?}。
 * 候选排名 + 方法 + 权重来源 + 硬约束否决 —— 观测证据（observed）与
 * 用户假设（assumed）在行级 basis 上可区分。防御式解析。
 */

interface DecisionRow {
  rank: number;
  name: string;
  score?: number | string;
  basis?: string;
}

interface DecisionPayload {
  method?: string;
  rows?: DecisionRow[];
  weightSource?: string;
  vetoes?: string[];
}

function parseDecision(raw: unknown): DecisionPayload | null {
  if (!raw || typeof raw !== 'object') return null;
  const rec = raw as Record<string, unknown>;
  const payload: DecisionPayload = {};
  if (typeof rec['method'] === 'string' && rec['method'].trim()) payload.method = rec['method'];
  if (typeof rec['weightSource'] === 'string' && rec['weightSource'].trim()) {
    payload.weightSource = rec['weightSource'];
  }
  const rowsRaw = rec['rows'];
  if (Array.isArray(rowsRaw) && rowsRaw.length > 0) {
    const rows: DecisionRow[] = [];
    for (const item of rowsRaw) {
      if (!item || typeof item !== 'object') continue;
      const r = item as Record<string, unknown>;
      if (typeof r['name'] !== 'string' || !r['name'].trim()) continue;
      const score = typeof r['score'] === 'number' || typeof r['score'] === 'string' ? r['score'] : undefined;
      rows.push({
        rank: typeof r['rank'] === 'number' ? (r['rank'] as number) : rows.length + 1,
        name: r['name'],
        score,
        basis: typeof r['basis'] === 'string' ? (r['basis'] as string) : undefined,
      });
    }
    if (rows.length > 0) payload.rows = rows;
  }
  const vetoesRaw = rec['vetoes'];
  if (Array.isArray(vetoesRaw)) {
    payload.vetoes = vetoesRaw.filter((v): v is string => typeof v === 'string' && !!v.trim());
  }
  return payload.rows || payload.vetoes ? payload : null;
}

function DecisionPanelView({ component, ctx }: { component: MapSpecComponent; ctx?: RendererContext }) {
  const patched = usePlacementPatchedComponent(component);
  const variant = resolveVariant(patched, 'default') === 'compact' ? 'compact' : 'default';
  const decision = parseDecision(patched.options?.['decision']);
  const title = decision?.method ? `决策（${decision.method}）` : '决策';

  return (
    <FloatingChrome
      component={patched}
      title={title}
      topSlotIndexes={ctx?.topSlotIndexes}
      testId="spec-chrome-decision-panel"
      dataVariant={variant}
      bodyClassName={variant === 'compact' ? 'p-1.5' : 'p-2'}
    >
      {decision ? (
        <div className={`flex flex-col ${variant === 'compact' ? 'gap-0.5' : 'gap-1'}`}>
          {decision.weightSource ? (
            <div className="px-1 text-micro text-map-chrome-ink-muted" data-testid="decision-weight-source">
              权重来源：{decision.weightSource}
            </div>
          ) : null}
          {decision.rows ? (
            <ol className="flex flex-col" role="list">
              {decision.rows.map((row, i) => (
                <li
                  key={`${row.name}#${i}`}
                  data-basis={row.basis}
                  data-vetoed={row.basis === 'vetoed' ? 'true' : undefined}
                  className={`flex items-baseline justify-between gap-2 px-1 py-0.5 ${
                    row.basis === 'vetoed'
                      ? 'text-map-chrome-ink-muted line-through'
                      : 'text-map-chrome-ink'
                  }`}
                >
                  <span className="min-w-0 truncate text-caption">
                    <span className="mr-1 tabular-nums text-map-chrome-ink-muted">{row.rank}.</span>
                    {row.name}
                  </span>
                  {row.score !== undefined ? (
                    <span className="shrink-0 tabular-nums text-caption font-medium">
                      {row.score}
                    </span>
                  ) : null}
                </li>
              ))}
            </ol>
          ) : null}
          {decision.vetoes && decision.vetoes.length > 0 ? (
            <div className="border-t border-map-chrome-line px-1 pt-1">
              <div className="text-micro text-map-chrome-ink-muted">硬约束否决：</div>
              {decision.vetoes.map((v, i) => (
                <div key={`veto#${i}`} className="text-caption text-map-chrome-ink-muted">
                  · {v}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : (
        <div
          className="flex min-h-12 items-center justify-center px-2 py-3 text-caption text-map-chrome-ink-muted"
          data-state="empty"
          role="status"
        >
          暂无决策结果
        </div>
      )}
    </FloatingChrome>
  );
}

function DecisionPanelRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  return <DecisionPanelView component={component} ctx={_ctx} />;
}

registerComponentRenderer('decision_panel', DecisionPanelRenderer);
