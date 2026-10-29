'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { resolveVariant } from './helpers';
import { FloatingChrome, usePlacementPatchedComponent } from './floating-chrome';
import type { RendererContext } from './types';
import { useT } from '@/lib/i18n/useT';

/**
 * design_candidates 渲染器（/goal carto-design M7）：options.design =
 * design_digest {count?, selected?, candidates: [{m, f, s, why?}],
 * blocked?, honored?}。表达模型层候选对比（为何选/为何弃/typed blocked）
 * —— 只读披露面：接受动作经 webgis_map_design_select 统一 action
 * （Agent 与 API 共享，面板不自带状态机）。防御式解析（坏载荷 → 空态卡）。
 */

interface DesignCandidateDigest {
  model: string;
  family: string;
  suitability: string;
  why?: string;
}

interface DesignDigest {
  count?: number;
  selected?: string;
  candidates: DesignCandidateDigest[];
  blocked?: string[];
  honored?: string[];
}

function parseDesign(raw: unknown): DesignDigest | null {
  if (!raw || typeof raw !== 'object') return null;
  const rec = raw as Record<string, unknown>;
  const candsRaw = rec['candidates'];
  if (!Array.isArray(candsRaw) || candsRaw.length === 0) return null;
  const candidates: DesignCandidateDigest[] = [];
  for (const item of candsRaw) {
    if (!item || typeof item !== 'object') continue;
    const r = item as Record<string, unknown>;
    if (typeof r['m'] !== 'string' || !r['m'].trim()) continue;
    candidates.push({
      model: r['m'],
      family: typeof r['f'] === 'string' ? r['f'] : '',
      suitability: typeof r['s'] === 'string' ? r['s'] : '',
      why: typeof r['why'] === 'string' && r['why'].trim() ? r['why'] : undefined,
    });
  }
  if (candidates.length === 0) return null;
  const out: DesignDigest = { candidates };
  if (typeof rec['count'] === 'number') out.count = rec['count'];
  if (typeof rec['selected'] === 'string' && rec['selected'].trim()) {
    out.selected = rec['selected'];
  }
  if (Array.isArray(rec['blocked'])) {
    out.blocked = rec['blocked'].filter(
      (b): b is string => typeof b === 'string' && !!b.trim(),
    );
  }
  if (Array.isArray(rec['honored'])) {
    out.honored = rec['honored'].filter(
      (h): h is string => typeof h === 'string' && !!h.trim(),
    );
  }
  return out;
}

function suitabilityLabel(s: string, t: (k: string) => string): string {
  if (s === 'suitable') return t('map.designCandidates.suitable');
  if (s === 'conditional') return t('map.designCandidates.conditional');
  if (s === 'blocked') return t('map.designCandidates.blocked');
  return s;
}

function DesignCandidatesView({
  component,
  ctx,
}: {
  component: MapSpecComponent;
  ctx?: RendererContext;
}) {
  const t = useT();
  const patched = usePlacementPatchedComponent(component);
  const variant =
    resolveVariant(patched, 'default') === 'compact' ? 'compact' : 'default';
  const design = parseDesign(patched.options?.['design']);

  return (
    <FloatingChrome
      component={patched}
      title={t('map.designCandidates.title')}
      topSlotIndexes={ctx?.topSlotIndexes}
      bottomSlotIndexes={ctx?.bottomSlotIndexes}
      testId="spec-chrome-design-candidates"
      dataVariant={variant}
      bodyClassName={variant === 'compact' ? 'p-1.5' : 'p-2'}
    >
      {!design ? (
        <div className="p-3 text-xs text-neutral-500">
          {t('map.designCandidates.none')}
        </div>
      ) : (
        <div
          className={variant === 'compact' ? 'p-2' : 'p-3'}
          style={{ minWidth: 208, maxWidth: 320 }}
        >
          <ul className="flex flex-col gap-1.5">
            {design.candidates.map((c) => {
              const isSelected = !!design.selected?.startsWith(c.model);
              const isBlocked = c.suitability === 'blocked';
              return (
                <li
                  key={c.model}
                  className="rounded border border-neutral-200 bg-white/80 px-2 py-1.5"
                  data-testid="design-candidate-row"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-neutral-800">
                      {c.model}
                      {isSelected ? (
                        <span className="ml-1 text-[10px] text-emerald-700">
                          ✓ {t('map.designCandidates.selected')}
                        </span>
                      ) : null}
                    </span>
                    <span
                      className={
                        isBlocked
                          ? 'rounded bg-red-50 px-1.5 py-0.5 text-[10px] text-red-700'
                          : c.suitability === 'conditional'
                            ? 'rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700'
                            : 'rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700'
                      }
                    >
                      {suitabilityLabel(c.suitability, t)}
                    </span>
                  </div>
                  {variant !== 'compact' && c.family ? (
                    <div className="mt-0.5 text-[10px] text-neutral-400">
                      {c.family}
                    </div>
                  ) : null}
                  {variant !== 'compact' && c.why ? (
                    <div className="mt-0.5 text-[11px] leading-snug text-neutral-600">
                      {c.why}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
          <div className="mt-2 text-[10px] leading-snug text-neutral-400">
            {t('map.designCandidates.acceptHint')}
          </div>
        </div>
      )}
    </FloatingChrome>
  );
}

export function DesignCandidatesRenderer(
  component: MapSpecComponent,
  ctx?: RendererContext,
) {
  return <DesignCandidatesView component={component} ctx={ctx} />;
}

registerComponentRenderer('design_candidates', DesignCandidatesRenderer);
