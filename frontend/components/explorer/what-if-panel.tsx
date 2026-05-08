"use client";

import { useState } from "react";
import type { WhatIfSimulationResult, SimulationViewMode } from "@/lib/types/explorer";

interface WhatIfPanelProps {
  result: WhatIfSimulationResult;
  onViewModeChange?: (mode: SimulationViewMode) => void;
}

const METRIC_LABELS: Record<string, string> = {
  housing_price: "房价",
  rent: "租金",
  commute_time: "通勤时间",
  commercial_vitality: "商业活力",
  education_access: "教育可达性",
  medical_access: "医疗可达性",
  living_quality: "居住质量",
  road_saturation: "道路饱和度",
  public_transit_usage: "公共交通使用率",
  air_quality: "空气质量",
  housing_demand: "住房需求",
  traffic_load: "交通负荷",
  school_demand: "学位需求",
  hospital_demand: "医疗需求",
  commercial_demand: "商业需求",
};

const VIEW_MODE_LABELS: Record<SimulationViewMode, string> = {
  baseline: "基准",
  simulated: "模拟",
  delta: "差异",
};

function formatValue(value: number, mode: SimulationViewMode): string {
  if (mode === "delta") {
    const sign = value >= 0 ? "+" : "";
    return `${sign}${value.toFixed(1)}%`;
  }
  return value.toFixed(1);
}

function MetricCard({
  metricKey,
  data,
  viewMode,
}: {
  metricKey: string;
  data: { baseline: number; simulated: number; delta_pct: number };
  viewMode: SimulationViewMode;
}) {
  const label = METRIC_LABELS[metricKey] || metricKey;

  let displayValue: string;
  let deltaClass = "";

  if (viewMode === "baseline") {
    displayValue = formatValue(data.baseline, viewMode);
  } else if (viewMode === "simulated") {
    displayValue = formatValue(data.simulated, viewMode);
  } else {
    displayValue = formatValue(data.delta_pct, viewMode);
    deltaClass =
      data.delta_pct > 0
        ? "text-red-400"
        : data.delta_pct < 0
          ? "text-green-400"
          : "text-white/60";
  }

  return (
    <div className="rounded-lg border border-white/10 bg-white/5 p-3">
      <div className="text-xs text-white/50">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${deltaClass || "text-white/90"}`}>
        {displayValue}
      </div>
      {viewMode !== "delta" && (
        <div
          className={`mt-0.5 text-xs ${
            data.delta_pct > 0
              ? "text-red-400"
              : data.delta_pct < 0
                ? "text-green-400"
                : "text-white/40"
          }`}
        >
          {data.delta_pct > 0 ? "+" : ""}
          {data.delta_pct.toFixed(1)}%
        </div>
      )}
    </div>
  );
}

export function WhatIfPanel({ result, onViewModeChange }: WhatIfPanelProps) {
  const [viewMode, setViewMode] = useState<SimulationViewMode>("baseline");

  function handleModeChange(mode: SimulationViewMode) {
    setViewMode(mode);
    onViewModeChange?.(mode);
  }

  return (
    <div className="rounded-xl border border-white/10 bg-black/40 p-4 backdrop-blur-sm">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-white/90">What-if 场景模拟</h2>
          <p className="mt-0.5 text-xs text-white/50">{result.scenario}</p>
        </div>
        <div className="flex rounded-lg border border-white/10 bg-white/5 p-0.5">
          {(Object.keys(VIEW_MODE_LABELS) as SimulationViewMode[]).map((mode) => (
            <button
              key={mode}
              onClick={() => handleModeChange(mode)}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                viewMode === mode
                  ? "bg-blue-500/20 text-blue-400"
                  : "text-white/50 hover:text-white/70"
              }`}
            >
              {VIEW_MODE_LABELS[mode]}
            </button>
          ))}
        </div>
      </div>

      {/* Impact area summary */}
      <div className="mt-4">
        <h3 className="text-xs font-medium uppercase tracking-wider text-white/40">
          影响范围
        </h3>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <div className="rounded-lg border border-white/10 bg-white/5 p-3">
            <div className="text-xs text-white/50">直接影响面积</div>
            <div className="mt-1 text-lg font-semibold text-white/90">
              {result.impact_summary.direct_area_km2.toFixed(2)} km²
            </div>
          </div>
          <div className="rounded-lg border border-white/10 bg-white/5 p-3">
            <div className="text-xs text-white/50">间接影响面积</div>
            <div className="mt-1 text-lg font-semibold text-white/90">
              {result.impact_summary.indirect_area_km2.toFixed(2)} km²
            </div>
          </div>
        </div>
      </div>

      {/* Metric cards */}
      <div className="mt-4">
        <h3 className="text-xs font-medium uppercase tracking-wider text-white/40">
          指标变化
        </h3>
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {Object.entries(result.metrics).map(([key, data]) => (
            <MetricCard key={key} metricKey={key} data={data} viewMode={viewMode} />
          ))}
        </div>
      </div>

      {/* Uncertainty disclaimer */}
      <div className="mt-4 rounded-lg border border-blue-500/20 bg-blue-500/5 p-3">
        <p className="text-xs font-medium text-blue-400/80">
          {result.uncertainty}
        </p>
      </div>

      {/* Applied rules */}
      <div className="mt-4">
        <h3 className="text-xs font-medium uppercase tracking-wider text-white/40">
          应用规则
        </h3>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {result.rules_applied.map((rule, index) => (
            <span
              key={index}
              className="rounded-md bg-blue-500/10 px-2 py-0.5 text-xs text-blue-400"
            >
              {rule}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
