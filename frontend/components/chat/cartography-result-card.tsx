'use client';

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
    <div className="my-2 p-3 rounded-md border border-edge-subtle bg-surface-raised">
      <div className="flex items-center gap-2 mb-2">
        <Palette className="h-4 w-4 text-status-accent" />
        <span className="text-body font-semibold text-ink truncate">{title}</span>
      </div>
      {spec && (
        <>
          <div className="flex items-center gap-1 mb-2">
            {colors.map((c, i) => (
              <div
                key={i}
                data-testid="card-swatch"
                className="w-5 h-3 rounded-sm ring-1 ring-edge-subtle"
                style={{ backgroundColor: c }}
              />
            ))}
          </div>
          <div className="flex items-center justify-between">
            <span className="text-body text-ink-muted">{summarize(spec)}</span>
            <button
              type="button"
              onClick={() => onFocus?.(layerId)}
              className="inline-flex items-center gap-1 text-body font-medium text-status-accent hover:underline"
            >
              <Target className="h-3 w-3" />
              高亮此图层
            </button>
          </div>
        </>
      )}
      {review && (
        <div
          className="mt-2 border-t border-edge-subtle pt-2"
          aria-label="制图质量"
          role="status"
          aria-live="polite"
        >
          <div className="flex items-center gap-1.5 text-body text-ink-muted">
            {reviewPassed ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-status-success" />
            ) : reviewUnknown ? (
              <HelpCircle className="h-3.5 w-3.5 text-status-warning" />
            ) : (
              <AlertTriangle className="h-3.5 w-3.5 text-status-danger" />
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
            <p key={`${check.rule}-${check.message}`} className="mt-1 text-caption text-status-danger">
              {check.message || check.rule}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
