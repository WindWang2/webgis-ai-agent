'use client';

import { useState } from 'react';
import { Clock, Play, Pause, RotateCcw, Target, Sparkles, Activity } from 'lucide-react';

export interface StDbscanResultStats {
  total_clusters?: number;
  clustered_points?: number;
  noise_points?: number;
  temporal_span_hours?: number;
  eps1_spatial_meters?: number;
  eps2_temporal_seconds?: number;
  min_samples?: number;
  [key: string]: unknown;
}

export interface StDbscanResultPayload {
  stats?: StDbscanResultStats;
  cluster_stats?: StDbscanResultStats;
  summary?: string;
  [key: string]: unknown;
}

interface Props {
  result: {
    result?: StDbscanResultPayload;
    metadata?: StDbscanResultPayload;
    stats?: StDbscanResultStats;
    cluster_stats?: StDbscanResultStats;
    summary?: string;
    [key: string]: unknown;
  } | null | undefined;
  layerId?: string;
  onFocus?: (layerId: string) => void;
  onFrameChange?: (framePct: number) => void;
}

export function StDbscanResultCard({ result, layerId, onFocus, onFrameChange }: Props) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [framePct, setFramePct] = useState(100);

  if (!result) return null;

  const payload = (result.result as StDbscanResultPayload) ?? (result.metadata as StDbscanResultPayload) ?? result;
  const stats = payload.stats ?? payload.cluster_stats ?? result.stats ?? result.cluster_stats ?? null;
  const summaryText = payload.summary ?? (typeof result.summary === 'string' ? result.summary : null);

  if (!stats && !summaryText) return null;

  const totalClusters = stats?.total_clusters ?? 0;
  const clusteredPoints = stats?.clustered_points ?? 0;
  const noisePoints = stats?.noise_points ?? 0;
  const temporalSpan = stats?.temporal_span_hours ?? 0;
  const eps1 = stats?.eps1_spatial_meters ?? 1000;
  const eps2 = stats?.eps2_temporal_seconds ?? 3600;

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = Number(e.target.value);
    setFramePct(val);
    if (onFrameChange) {
      onFrameChange(val);
    }
  };

  const togglePlay = () => {
    setIsPlaying(!isPlaying);
  };

  const handleReset = () => {
    setFramePct(0);
    if (onFrameChange) {
      onFrameChange(0);
    }
  };

  return (
    <div className="my-2 p-3 rounded-lg border border-slate-200/80 bg-white/70 dark:border-slate-800 dark:bg-slate-900/70 backdrop-blur-sm text-sm">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5 font-semibold text-slate-800 dark:text-slate-200">
          <Activity className="h-4 w-4 text-emerald-500 shrink-0" />
          <span>ST-DBSCAN 时空聚类分析</span>
        </div>
        <span className="text-[12px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-300 font-mono font-medium">
          R={eps1}m / T={eps2}s
        </span>
      </div>

      {/* Stats Badges Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5 mb-2.5">
        <div data-testid="st-badge-clusters" className="p-1.5 rounded border bg-emerald-500/10 border-emerald-500/30 flex flex-col justify-between">
          <span className="text-[11px] font-bold text-emerald-600 dark:text-emerald-400">聚类簇数</span>
          <span className="text-[13px] font-mono font-bold text-emerald-700 dark:text-emerald-300">{totalClusters}</span>
        </div>
        <div data-testid="st-badge-clustered" className="p-1.5 rounded border bg-blue-500/10 border-blue-500/30 flex flex-col justify-between">
          <span className="text-[11px] font-bold text-blue-600 dark:text-blue-400">聚类点数</span>
          <span className="text-[13px] font-mono font-bold text-blue-700 dark:text-blue-300">{clusteredPoints}</span>
        </div>
        <div data-testid="st-badge-noise" className="p-1.5 rounded border bg-amber-500/10 border-amber-500/30 flex flex-col justify-between">
          <span className="text-[11px] font-bold text-amber-600 dark:text-amber-400">噪声点数</span>
          <span className="text-[13px] font-mono font-bold text-amber-700 dark:text-amber-300">{noisePoints}</span>
        </div>
        <div data-testid="st-badge-span" className="p-1.5 rounded border bg-purple-500/10 border-purple-500/30 flex flex-col justify-between">
          <span className="text-[11px] font-bold text-purple-600 dark:text-purple-400">跨度(h)</span>
          <span className="text-[13px] font-mono font-bold text-purple-700 dark:text-purple-300">{temporalSpan}h</span>
        </div>
      </div>

      {/* Timeline Controls */}
      <div className="p-2 mb-2 rounded border border-slate-100 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-800/50">
        <div className="flex items-center justify-between mb-1.5 text-[11px] text-slate-500 dark:text-slate-400 font-mono">
          <div className="flex items-center gap-1">
            <Clock className="h-3 w-3 text-slate-400" />
            <span>时间轴帧控制</span>
          </div>
          <span>{framePct}%</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            data-testid="play-pause-button"
            aria-label={isPlaying ? '暂停演变动画' : '播放演变动画'}
            onClick={togglePlay}
            className="p-1 rounded bg-emerald-500 text-white hover:bg-emerald-600 transition-colors"
          >
            {isPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          </button>
          <button
            type="button"
            data-testid="reset-button"
            aria-label="重置时间轴"
            onClick={handleReset}
            className="p-1 rounded bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-300 dark:hover:bg-slate-600 transition-colors"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
          <input
            type="range"
            aria-label="时间轴滑块"
            min="0"
            max="100"
            value={framePct}
            onChange={handleSliderChange}
            className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-emerald-500"
          />
        </div>
      </div>

      {/* Summary Note */}
      {summaryText && (
        <div className="flex items-start gap-1.5 mb-2 text-[12px] text-slate-600 dark:text-slate-300 leading-relaxed bg-slate-50/80 dark:bg-slate-800/40 p-2 rounded border border-slate-100 dark:border-slate-800">
          <Sparkles className="h-3.5 w-3.5 text-amber-500 shrink-0 mt-0.5" />
          <span>{summaryText}</span>
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between pt-1 border-t border-slate-100 dark:border-slate-800 text-[12px]">
        <span className="text-slate-400 dark:text-slate-500 font-mono">
          要素覆盖: {clusteredPoints + noisePoints} 个点
        </span>
        {layerId && onFocus && (
          <button
            type="button"
            onClick={() => onFocus(layerId)}
            className="inline-flex items-center gap-1 font-medium text-emerald-600 hover:text-emerald-700 dark:text-emerald-400 dark:hover:text-emerald-300 hover:underline transition-colors"
          >
            <Target className="h-3 w-3" />
            高亮图层
          </button>
        )}
      </div>
    </div>
  );
}

export default StDbscanResultCard;
