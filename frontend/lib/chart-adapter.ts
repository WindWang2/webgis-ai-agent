import { devOnly } from "@/lib/utils/logger";
import type { ChartData, ChartKind } from "@/lib/types";

// V4：kind 词表（chart_kinds 单一权威的前端镜像；violin planned 不入）
const VALID_CHART_TYPES = new Set<ChartKind>([
  'bar', 'horizontal_bar', 'grouped_bar', 'stacked_bar',
  'line', 'area', 'scatter', 'histogram', 'box_plot',
  'pie', 'donut', 'radar', 'rose', 'timeseries', 'cumulative',
  'heat_matrix', 'kpi_card', 'ranking_list',
]);

// 别名归一（与后端 chart_kinds.aliases 同表）
const CHART_KIND_ALIASES: Record<string, ChartKind> = {
  hbar: 'horizontal_bar',
  doughnut: 'donut',
  time_series: 'timeseries',
};

// Runtime validation adapter - replaces unsafe "as ChartData" casts.
// Lives outside chart-renderer.tsx so chat-tab can keep this tiny pure
// function in the first-load bundle while recharts itself loads on demand
// (frontend bundle-slimming).
export function adaptChartData(raw: unknown): ChartData | null {
  try {
    if (!raw || typeof raw !== "object") return null

    const raw0 = raw as any
    // V4：kind 别名归一 + series/stacked 透传
    const type: ChartKind | undefined =
      VALID_CHART_TYPES.has(raw0.type) ? raw0.type : CHART_KIND_ALIASES[String(raw0.type)]

    if (!type || !VALID_CHART_TYPES.has(type)) {
      devOnly.warn("Invalid chart type:", raw0.type)
      return null
    }
    const { title, data, x_label, y_label, series, stacked } = raw0

    // V4 多序列：可选字段，结构宽松校验（空数组拒绝）
    if (series !== undefined) {
      if (!Array.isArray(series) || series.length === 0) {
        devOnly.warn("Invalid chart series")
        return null
      }
      for (const ser of series) {
        if (!ser || typeof ser.name !== 'string' || !Array.isArray(ser.data) || ser.data.length === 0) {
          devOnly.warn("Invalid chart series entry")
          return null
        }
      }
    }

    // Validate title (sanitized by backend, but double-check)
    if (!title || typeof title !== "string" || title.length === 0) {
      devOnly.warn("Invalid chart title")
      return null
    }

    // Validate data array
    if (!Array.isArray(data) || data.length === 0) {
      devOnly.warn("Invalid chart data")
      return null
    }

    // Validate each data point has required fields
    for (const point of data) {
      if (!point || typeof point !== "object") {
        return null
      }
      if (type === "scatter") {
        if (typeof point.x !== "number" || typeof point.y !== "number" || typeof point.name !== "string") {
          return null
        }
      } else {
        if (typeof point.value !== "number" || typeof point.name !== "string") {
          return null
        }
      }
    }

    return {
      type,
      title: String(title).slice(0, 200), // Additional length protection
      data,
      ...(Array.isArray(series) && { series }),
      ...(stacked === true && { stacked: true }),
      ...(x_label !== undefined && { x_label: String(x_label).slice(0, 200) }),
      ...(y_label !== undefined && { y_label: String(y_label).slice(0, 200) }),
    }
  } catch (e) {
    devOnly.error("Failed to validate chart data:", e)
    return null
  }
}
