"use client"

import type { ChartData, ChartDataPoint } from "@/lib/types";
import { adaptChartData } from "@/lib/chart-adapter";
import { ChartCore, isChartTypeSupported } from "./chart-core";

export type { ChartData, ChartDataPoint };
export { adaptChartData };

// 渲染核（Recharts 子组件 + 主题派生）已抽到 chart-core.tsx —— chat 消息
// 与地图 chart_panel 共用同一实现，不出现第二套图表 schema/主题。
export { ChartCore } from "./chart-core";

export function ChartRenderer({ chart }: { chart: ChartData }) {
  // Show error instead of silent null for debugging
  if (!isChartTypeSupported(chart.type)) {
    return (
      <div className="mt-2 rounded-lg border border-red-500/20 bg-red-950/30 p-3">
        <h4 className="text-xs font-medium text-red-300">{`无法渲染图表：未支持的类型 "${chart.type}"`}</h4>
      </div>
    )
  }

  return (
    <div className="mt-2 rounded-lg border border-map-chrome-border bg-[color:var(--surface-sunken)] p-3">
      <h4 className="mb-2 text-xs font-medium text-[color:var(--text-primary)]">{chart.title}</h4>
      <ChartCore chart={chart} />
    </div>
  )
}
