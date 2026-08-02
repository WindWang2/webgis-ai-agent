'use client';

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

const CLUSTER_CONFIG: Record<string, { label: string; bg: string; text: string; desc: string }> = {
  HH: { label: '高-高热点', bg: 'bg-red-500/15 border-red-500/30', text: 'text-red-600 dark:text-red-400', desc: '显著高值聚集区' },
  LL: { label: '低-低冷点', bg: 'bg-blue-500/15 border-blue-500/30', text: 'text-blue-600 dark:text-blue-400', desc: '显著低值聚集区' },
  HL: { label: '高-低异常', bg: 'bg-orange-500/15 border-orange-500/30', text: 'text-orange-600 dark:text-orange-400', desc: '高值包围低值' },
  LH: { label: '低-高异常', bg: 'bg-cyan-500/15 border-cyan-500/30', text: 'text-cyan-600 dark:text-cyan-400', desc: '低值包围高值' },
  NS: { label: '不显著', bg: 'bg-slate-500/10 border-slate-500/20', text: 'text-slate-500 dark:text-slate-400', desc: '无显著空间关联' },
};

export function H3LisaResultCard({ result, layerId, onFocus }: Props) {
  if (!result) return null;

  // Extract counts from result body or metadata
  const payload = (result.result as H3LisaResultPayload) ?? (result.metadata as H3LisaResultPayload) ?? result;
  const counts = payload.cluster_counts ?? result.cluster_counts ?? null;
  const valueField = payload.value_field ?? result.value_field ?? '指标';
  const summaryText = payload.summary ?? (typeof result.summary === 'string' ? result.summary : null);

  if (!counts && !summaryText) return null;

  const totalSig = counts
    ? (counts.HH ?? 0) + (counts.LL ?? 0) + (counts.HL ?? 0) + (counts.LH ?? 0)
    : 0;

  return (
    <div className="my-2 p-3 rounded-lg border border-slate-200/80 bg-white/70 dark:border-slate-800 dark:bg-slate-900/70 backdrop-blur-sm text-sm">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5 font-semibold text-slate-800 dark:text-slate-200">
          <Hexagon className="h-4 w-4 text-indigo-500 shrink-0" />
          <span>H3 LISA 空间聚类分析</span>
        </div>
        <span className="text-[12px] px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-600 dark:bg-indigo-950/60 dark:text-indigo-300 font-mono font-medium">
          字段: {valueField}
        </span>
      </div>

      {/* Cluster Swatches / Badges */}
      {counts && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5 mb-2.5">
          {(['HH', 'LL', 'HL', 'LH'] as const).map((type) => {
            const count = counts[type] ?? 0;
            const cfg = CLUSTER_CONFIG[type];
            return (
              <div
                key={type}
                data-testid={`lisa-badge-${type}`}
                className={`p-1.5 rounded border ${cfg.bg} flex flex-col justify-between transition-all`}
              >
                <div className="flex items-center justify-between">
                  <span className={`text-[11px] font-bold ${cfg.text}`}>{cfg.label}</span>
                  <span className={`text-[13px] font-mono font-bold ${cfg.text}`}>{count}</span>
                </div>
                <span className="text-[10px] text-slate-400 dark:text-slate-500 truncate mt-0.5">{cfg.desc}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Summary note */}
      {summaryText && (
        <div className="flex items-start gap-1.5 mb-2 text-[12px] text-slate-600 dark:text-slate-300 leading-relaxed bg-slate-50/80 dark:bg-slate-800/40 p-2 rounded border border-slate-100 dark:border-slate-800">
          <Sparkles className="h-3.5 w-3.5 text-amber-500 shrink-0 mt-0.5" />
          <span>{summaryText}</span>
        </div>
      )}

      {/* Action Footer */}
      <div className="flex items-center justify-between pt-1 border-t border-slate-100 dark:border-slate-800 text-[12px]">
        <span className="text-slate-400 dark:text-slate-500 font-mono">
          {totalSig > 0 ? `累计显著聚类: ${totalSig} 个网格` : '未发现显著聚集'}
        </span>
        {layerId && onFocus && (
          <button
            type="button"
            onClick={() => onFocus(layerId)}
            className="inline-flex items-center gap-1 font-medium text-indigo-600 hover:text-indigo-700 dark:text-indigo-400 dark:hover:text-indigo-300 hover:underline transition-colors"
          >
            <Target className="h-3 w-3" />
            高亮图层
          </button>
        )}
      </div>
    </div>
  );
}

export default H3LisaResultCard;
