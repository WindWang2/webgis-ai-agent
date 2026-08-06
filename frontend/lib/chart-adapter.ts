import { devOnly } from "@/lib/utils/logger";
import type { ChartData } from "@/lib/types";

const VALID_CHART_TYPES = new Set(["bar", "line", "pie", "scatter"] as const)

// Runtime validation adapter - replaces unsafe "as ChartData" casts.
// Lives outside chart-renderer.tsx so chat-tab can keep this tiny pure
// function in the first-load bundle while recharts itself loads on demand
// (frontend bundle-slimming).
export function adaptChartData(raw: unknown): ChartData | null {
  try {
    if (!raw || typeof raw !== "object") return null

    const { type, title, data, x_label, y_label } = raw as any

    // Validate type
    if (!type || !VALID_CHART_TYPES.has(type)) {
      devOnly.warn("Invalid chart type:", type)
      return null
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
      ...(x_label !== undefined && { x_label: String(x_label).slice(0, 200) }),
      ...(y_label !== undefined && { y_label: String(y_label).slice(0, 200) }),
    }
  } catch (e) {
    devOnly.error("Failed to validate chart data:", e)
    return null
  }
}
