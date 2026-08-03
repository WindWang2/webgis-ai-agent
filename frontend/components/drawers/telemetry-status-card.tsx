"use client";

import React from "react";
import { Activity, Database, ShieldCheck, RefreshCw, Cpu, Layers } from "lucide-react";

export interface ToolMetricSnapshot {
  count: number;
  total_ms: number;
  max_ms: number;
  hit_count: number;
  error_count: number;
}

export interface SpatialCacheInfo {
  hits: number;
  misses: number;
  size: number;
  maxsize: number;
}

export interface HarnessTelemetryMetrics {
  /** 0-100 percentage metrics. Rendered with a ``%`` suffix. */
  rates: Record<string, number>;
  /** Raw integer counts. Rendered without a ``%`` suffix. */
  counts: Record<string, number>;
}

export interface TelemetryDigest {
  success: boolean;
  tool_metrics: Record<string, ToolMetricSnapshot>;
  spatial_cache: SpatialCacheInfo;
  harness_enabled: boolean;
  harness_metrics?: HarnessTelemetryMetrics | null;
}

export interface TelemetryStatusCardProps {
  digest?: TelemetryDigest | null;
  onRefresh?: () => void;
  isLoading?: boolean;
  className?: string;
}

export function TelemetryStatusCard({
  digest,
  onRefresh,
  isLoading = false,
  className = "",
}: TelemetryStatusCardProps) {
  if (!digest) {
    return (
      <div className={`backdrop-blur-md bg-slate-900/80 border border-slate-700/50 rounded-xl p-4 text-slate-300 text-sm shadow-xl ${className}`}>
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 font-medium">
            <Activity className="w-4 h-4 text-cyan-400" />
            生产端性能与评估遥测
          </span>
          {onRefresh && (
            <button
              onClick={onRefresh}
              disabled={isLoading}
              className="p-1.5 hover:bg-slate-800 rounded-lg transition-colors text-slate-400 hover:text-slate-200"
              title="刷新数据"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} />
            </button>
          )}
        </div>
        <p className="mt-2 text-xs text-slate-400">暂无遥测数据，请点击刷新加载。</p>
      </div>
    );
  }

  const { tool_metrics, spatial_cache, harness_enabled, harness_metrics } = digest;
  const totalCacheRequests = (spatial_cache?.hits || 0) + (spatial_cache?.misses || 0);
  const cacheHitRatio = totalCacheRequests > 0
    ? Math.round(((spatial_cache?.hits || 0) / totalCacheRequests) * 100)
    : 100;

  const toolEntries = Object.entries(tool_metrics || {});

  return (
    <div className={`backdrop-blur-md bg-slate-900/80 border border-slate-700/50 rounded-xl p-5 text-slate-100 shadow-xl space-y-4 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 pb-3">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-cyan-400" />
          <h3 className="font-semibold text-sm tracking-wide text-slate-100">
            生产端性能与评估遥测
          </h3>
        </div>
        {onRefresh && (
          <button
            onClick={onRefresh}
            disabled={isLoading}
            className="flex items-center gap-1 text-xs bg-slate-800 hover:bg-slate-700 px-2.5 py-1.5 rounded-md transition-colors text-slate-300 border border-slate-700/60"
          >
            <RefreshCw className={`w-3 h-3 ${isLoading ? "animate-spin" : ""}`} />
            <span>刷新</span>
          </button>
        )}
      </div>

      {/* Grid: Cache Stats & Harness Status */}
      <div className="grid grid-cols-2 gap-3">
        {/* Spatial Cache Card */}
        <div className="bg-slate-950/50 border border-slate-800/80 rounded-lg p-3 space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-medium text-slate-400">
            <Database className="w-3.5 h-3.5 text-amber-400" />
            <span>距离矩阵 LRU 缓存</span>
          </div>
          <div className="flex items-baseline justify-between pt-1">
            <span className="text-lg font-bold text-amber-400">{cacheHitRatio}%</span>
            <span className="text-[11px] text-slate-400">
              容量 {spatial_cache?.size || 0}/{spatial_cache?.maxsize || 128}
            </span>
          </div>
          <p className="text-[11px] text-slate-500">
            Hits: {spatial_cache?.hits || 0} | Misses: {spatial_cache?.misses || 0}
          </p>
        </div>

        {/* Harness Gate Status Card */}
        <div className="bg-slate-950/50 border border-slate-800/80 rounded-lg p-3 space-y-1">
          <div className="flex items-center gap-1.5 text-xs font-medium text-slate-400">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
            <span>Pi Agent 评估 Harness</span>
          </div>
          <div className="flex items-baseline justify-between pt-1">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded ${harness_enabled ? "bg-emerald-950 text-emerald-400 border border-emerald-800/60" : "bg-slate-800 text-slate-400"}`}>
              {harness_enabled ? "已开启 (Live)" : "未启用 (Opt-in)"}
            </span>
          </div>
          <p className="text-[11px] text-slate-500 truncate">
            {harness_enabled ? "生产实时遥测采集" : "PI_HARNESS_ENABLED=1"}
          </p>
        </div>
      </div>

      {/* Harness 5-Metric Scores (if present) */}
      {harness_metrics && (
        <div className="bg-slate-950/40 border border-slate-800/60 rounded-lg p-3 space-y-2">
          <span className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
            <Cpu className="w-3.5 h-3.5 text-emerald-400" />
            5-维 GIS 评估质量指标
          </span>
          <div className="grid grid-cols-2 gap-2 text-xs">
            {Object.entries(harness_metrics.rates || {}).map(([name, score]) => (
              <div key={name} className="flex justify-between items-center bg-slate-900/60 p-1.5 rounded">
                <span className="text-slate-400 text-[11px] truncate">{name}</span>
                <span className="font-mono font-semibold text-emerald-400">{score}%</span>
              </div>
            ))}
          </div>
          {/* Raw counts - rendered WITHOUT % (U5 fix: previously shown as percentages). */}
          {Object.entries(harness_metrics.counts || {}).length > 0 && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1 border-t border-slate-800/60">
              {Object.entries(harness_metrics.counts || {}).map(([name, count]) => (
                <span key={name} className="text-[11px] text-slate-400">
                  <span className="text-slate-500">{name}: </span>
                  <span className="font-mono text-slate-200">{Math.round(count)}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tool Call Metrics Table */}
      <div className="space-y-2">
        <span className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
          <Layers className="w-3.5 h-3.5 text-cyan-400" />
          GIS 工具调用统计 ({toolEntries.length})
        </span>

        {toolEntries.length === 0 ? (
          <p className="text-xs text-slate-500 italic py-2">暂无工具调用记录</p>
        ) : (
          <div className="max-h-40 overflow-y-auto pr-1 space-y-1 text-xs">
            {toolEntries.map(([name, stat]) => {
              const avgMs = stat.count > 0 ? Math.round(stat.total_ms / stat.count) : 0;
              return (
                <div
                  key={name}
                  className="flex items-center justify-between bg-slate-950/40 hover:bg-slate-950/70 p-2 rounded-lg border border-slate-800/40 transition-colors"
                >
                  <span className="font-mono text-slate-200 truncate max-w-[140px] text-[11px]" title={name}>
                    {name}
                  </span>
                  <div className="flex items-center gap-3 text-[11px] text-slate-400">
                    <span>{stat.count} 次</span>
                    <span className="text-cyan-400 font-mono">{avgMs} ms</span>
                    {stat.error_count > 0 && (
                      <span className="text-rose-400 font-medium">! {stat.error_count}</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
