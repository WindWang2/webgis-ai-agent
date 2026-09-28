'use client';
import { useT } from '@/lib/i18n/useT';

import { Hexagon, Target, Sparkles } from 'lucide-react';

export interface H3LisaResultPayload {
  cluster_counts?: Record<string, number>;
  value_field?: string;
  summary?: string;
  dominant_pattern?: string;
  total_hexes?: number;
  [key: string]: unknown;
}

interface Props {
  result: {
    result?: H3LisaResultPayload;
    metadata?: H3LisaResultPayload;
    summary?: string;
    cluster_counts?: Record<string, number>;
    value_field?: string;
    [key: string]: unknown;
  } | null | undefined;
  layerId?: string;
  onFocus?: (layerId: string) => void;
}

const CLUSTER_CONFIG: Record<string, { labelKey: string; descKey: string; bg: string; text: string }> = {
  /* C：聚类色板收敛到 V4 status 词汇（红=critical / 蓝=info / 橙=warning /
     灰=neutral，均换 AA 达标值）。LH 的 cyan 在 V4 里没有对应 token，为保留
     四类聚类的色相区分而保留原色相，浅色档加深一档（cyan-700）过 AA。 */
  HH: { labelKey: 'lisa.hh', descKey: 'lisa.desc.hh', bg: 'bg-status-critical-soft border-status-critical-border', text: 'text-status-critical' },
  LL: { labelKey: 'lisa.ll', descKey: 'lisa.desc.ll', bg: 'bg-status-info-soft border-status-info-border', text: 'text-status-info' },
  HL: { labelKey: 'lisa.hl', descKey: 'lisa.desc.hl', bg: 'bg-status-warning-soft border-status-warning-border', text: 'text-status-warning' },
  LH: { labelKey: 'lisa.lh', descKey: 'lisa.desc.lh', bg: 'bg-cyan-500/15 border-cyan-500/30', text: 'text-cyan-700 dark:text-cyan-400' },
  NS: { labelKey: 'lisa.ns', descKey: 'lisa.desc.ns', bg: 'bg-status-neutral-soft border-status-neutral-border', text: 'text-status-neutral' },
};

export function H3LisaResultCard({ result, layerId, onFocus }: Props) {
  const t = useT();
  if (!result) return null;

  // Extract counts from result body or metadata
  const payload = (result.result as H3LisaResultPayload) ?? (result.metadata as H3LisaResultPayload) ?? result;
  const counts = payload.cluster_counts ?? result.cluster_counts ?? null;
  const valueField = payload.value_field ?? result.value_field ?? t('chat.lisa.fieldFallback');
  const summaryText = payload.summary ?? (typeof result.summary === 'string' ? result.summary : null);

  if (!counts && !summaryText) return null;

  const totalSig = counts
    ? (counts.HH ?? 0) + (counts.LL ?? 0) + (counts.HL ?? 0) + (counts.LH ?? 0)
    : 0;

  return (
    /* C：去掉 backdrop-blur-md，把 bg-white/80 dark:bg-slate-900/80 半透明对
       收敛为单一语义 token —— 结果卡浮在聊天气泡上，半透明+blur 只增加合成
       成本且让底下的正文透出来。 */
    <div className="my-2 p-3.5 rounded-md border border-edge-subtle bg-surface-raised text-body shadow-raised transition-all">
      {/* Header */}
      <div className="flex items-center justify-between mb-2.5">
        <div className="flex items-center gap-1.5 font-semibold text-ink">
          <Hexagon className="h-4 w-4 text-status-info shrink-0" />
          <span>{t('chat.lisa.title')}</span>
        </div>
        <span className="text-meta px-2 py-0.5 rounded-pill bg-status-info-soft text-status-info font-mono font-medium">
          {t('chat.lisa.field')} {valueField}
        </span>
      </div>

      {/* Cluster Swatches / Badges */}
      {counts && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
          {(['HH', 'LL', 'HL', 'LH'] as const).map((type) => {
            const count = counts[type] ?? 0;
            const cfg = CLUSTER_CONFIG[type];
            return (
              <div
                key={type}
                data-testid={`lisa-badge-${type}`}
                className={`p-2 rounded-md border ${cfg.bg} flex flex-col justify-between transition-all`}
              >
                <div className="flex items-center justify-between">
                  <span className={`text-meta font-bold ${cfg.text}`}>{t(`chat.${cfg.labelKey}`)}</span>
                  <span className={`text-body font-mono font-bold ${cfg.text}`}>{count}</span>
                </div>
                <span className="text-meta text-ink-muted truncate mt-0.5">{t(`chat.${cfg.descKey}`)}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Summary note */}
      {summaryText && (
        <div className="flex items-start gap-2 mb-2.5 text-body text-ink-secondary leading-relaxed bg-surface-sunken p-2.5 rounded-md border border-edge-subtle">
          <Sparkles className="h-4 w-4 text-status-warning shrink-0 mt-0.5" />
          <span>{summaryText}</span>
        </div>
      )}

      {/* Action Footer */}
      <div className="flex items-center justify-between pt-1.5 border-t border-edge-subtle text-meta">
        <span className="text-ink-muted font-mono">
          {totalSig > 0
            ? t('chat.lisa.totalSig', { count: totalSig })
            : t('chat.lisa.noCluster')}
        </span>
        {layerId && onFocus && (
          <button
            type="button"
            onClick={() => onFocus(layerId)}
            className="inline-flex items-center gap-1 font-medium text-status-info hover:underline transition-colors"
          >
            <Target className="h-3.5 w-3.5" />
            {t('chat.carto.highlightLayer')}
          </button>
        )}
      </div>
    </div>
  );
}

export default H3LisaResultCard;
