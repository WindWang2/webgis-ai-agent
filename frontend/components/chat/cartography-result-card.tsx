'use client';

import React from 'react';
import { AlertTriangle, CheckCircle2, HelpCircle, Palette, Target } from 'lucide-react';
import type { LegendSpec } from '@/lib/map-kit/types';

interface Props {
  result: {
    legend_spec?: LegendSpec;
    layer_meta?: { title?: string };
    cartographic_review?: {
      stage?: string;
      status?: string;
      repair_count?: number;
      checks?: Array<{ rule?: string; status?: string; message?: string }>;
    };
  } | null | undefined;
  layerId: string;
  onFocus?: (layerId: string) => void;
}

function summarize(spec: LegendSpec): string {
  switch (spec.type) {
    case 'graduated':
      return `${spec.field} · ${spec.breaks.length - 1} 分级`;
    case 'continuous':
      return `${spec.field ?? '密度'} · 连续色带`;
    case 'categorical':
      return `${spec.field} · ${spec.categories.length} 类`;
    case 'divergent':
      return `${spec.field ?? '指标'} · 发散色带`;
  }
}

function swatches(spec: LegendSpec): string[] {
  switch (spec.type) {
    case 'graduated':
    case 'continuous':
    case 'divergent':
      return spec.palette_colors;
    case 'categorical':
      return spec.categories.map((c) => c.color);
  }
}

export function CartographyResultCard({ result, layerId, onFocus }: Props) {
  const spec = result?.legend_spec;
  const review = result?.cartographic_review;
  if (!spec && !review) return null;
  const title = result?.layer_meta?.title ?? '专题图';
  const colors = spec ? swatches(spec) : [];
  const reviewPassed = review?.status === 'passed' || review?.status === 'passed_with_warnings';
  const desiredOnly = review?.stage === 'desired_state';
  const reviewUnknown = !review?.status || review.status === 'not_evaluated' || review.status === 'partial';
  const failedChecks = (review?.checks ?? []).filter((check) => check.status === 'fail').slice(0, 2);

  return (
    <div className="my-2 p-3.5 rounded-md border border-edge-subtle bg-surface-raised shadow-raised transition-all">
      {/* Header */}
      <div className="flex items-center gap-2 mb-2.5">
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-status-accent-soft text-status-accent">
          <Palette className="h-3.5 w-3.5" />
        </div>
        <span className="text-body font-semibold text-ink truncate">{title}</span>
      </div>

      {spec && (
        <>
          <div className="flex items-center gap-1.5 mb-2.5">
            {colors.map((c, i) => (
              <div
                key={i}
                data-testid="card-swatch"
                className="w-6 h-3.5 rounded-sm ring-1 ring-edge-subtle shadow-xs"
                style={{ backgroundColor: c }}
              />
            ))}
          </div>
          <div className="flex items-center justify-between pt-1">
            <span className="text-caption text-ink-muted font-medium">{summarize(spec)}</span>
            {onFocus && layerId ? (
              <button
                type="button"
                onClick={() => onFocus(layerId)}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-caption font-medium text-status-accent hover:bg-status-accent-soft transition-all cursor-pointer"
              >
                <Target className="h-3.5 w-3.5" />
                高亮此图层
              </button>
            ) : (
              <button
                type="button"
                disabled
                aria-disabled="true"
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-caption font-medium text-ink-disabled cursor-not-allowed"
                title="无可聚焦图层"
              >
                <Target className="h-3.5 w-3.5" />
                高亮此图层
              </button>
            )}
          </div>
        </>
      )}

      {review && (
        <div
          className="mt-2.5 border-t border-edge-subtle pt-2.5"
          aria-label="制图质量"
          role="status"
          aria-live="polite"
        >
          <div className="flex items-center gap-1.5 text-caption text-ink-secondary">
            {reviewPassed ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-status-success shrink-0" />
            ) : reviewUnknown ? (
              <HelpCircle className="h-3.5 w-3.5 text-status-warning shrink-0" />
            ) : (
              <AlertTriangle className="h-3.5 w-3.5 text-status-critical shrink-0" />
            )}
            <span>
              {reviewPassed
                ? desiredOnly
                  ? `制图结构检查：通过${review.repair_count ? `（已自动修复 ${review.repair_count} 项）` : ''}，等待运行时验证`
                  : `制图质量：通过${review.repair_count ? `（已自动修复 ${review.repair_count} 项）` : ''}`
                : reviewUnknown
                ? review.status === 'partial'
                  ? '制图质量：证据不完整'
                  : '制图质量：未评估'
                : '地图需要处理'}
            </span>
          </div>
          {failedChecks.map((check) => (
            <p key={`${check.rule}-${check.message}`} className="mt-1 text-caption text-status-critical">
              {check.message || check.rule}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

export default CartographyResultCard;
