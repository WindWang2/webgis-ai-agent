"use client"

/**
 * ChartCore —— Recharts 渲染核（#D2 图表协议复用）。
 *
 * 从 chart-renderer.tsx 原样抽出的 4 个 Render* 子组件 + 主题派生
 * （#741/#807）：chat 消息与地图 chart_panel 共用同一套图表 schema /
 * 主题，不出现第二套图表实现。chart-renderer.tsx 只是薄壳（卡片 + 标题）。
 */

import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend,
} from "recharts"

import type { ChartData } from "@/lib/types"
import { useHudStore } from "@/lib/store/useHudStore"

const COLORS = [
  "#06b6d4", "#22d3ee", "#67e8f9", "#a5f3fc",
  "#0891b2", "#0e7490", "#155e75", "#164e63",
]

// #741: recharts can't consume CSS vars in SVG tick fills directly — read
// the computed token at module/init time via a helper so charts follow the
// active theme (light or dark) instead of hard-coded dark-cyan values that
// were near-unreadable in light theme.
function themeColor(varName: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  return value || fallback;
}

// #807: 主题键参数化 —— themeColor 读的是渲染时刻的 computed token；外层
// ChatMessageItem 是 memo 边界，主题切换不会跨过它，旧值会一直滞留。
// 各 Render* 子组件以当前 theme 为键重派生样式。
function tickStyle(_themeKey?: string) {
  return { fill: themeColor("--text-muted", "#5b6b82"), fontSize: 13 };
}

function tooltipStyle(_themeKey?: string) {
  return {
    contentStyle: {
      backgroundColor: themeColor("--surface-raised", "#ffffff"),
      border: "1px solid rgba(100,116,139,0.3)",
      borderRadius: "6px",
      color: themeColor("--text-primary", "#1c2733"),
      fontSize: "14px",
    },
  };
}

interface RenderProps {
  chart: ChartData;
  height: number | `${number}%`;
}

function RenderBarChart({ chart, height }: RenderProps) {
  // #807: 自订阅主题 —— 外层 ChatMessageItem 是 memo 边界，主题切换不会
  // 跨过它；订阅后本组件在 toggle 时重派生 tick/tooltip 色。
  const theme = useHudStore((s) => s.theme);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={chart.data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(6,182,212,0.15)" />
        <XAxis dataKey="name" tick={tickStyle(theme)} />
        <YAxis tick={tickStyle(theme)} label={chart.y_label ? { value: chart.y_label, angle: -90, position: "insideLeft", ...tickStyle() } : undefined} />
        <Tooltip {...tooltipStyle(theme)} />
        <Bar dataKey="value" fill="#06b6d4" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function RenderLineChart({ chart, height }: RenderProps) {
  // #807: 自订阅主题 —— 同上。
  const theme = useHudStore((s) => s.theme);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={chart.data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(6,182,212,0.15)" />
        <XAxis dataKey="name" tick={tickStyle(theme)} />
        <YAxis tick={tickStyle(theme)} label={chart.y_label ? { value: chart.y_label, angle: -90, position: "insideLeft", ...tickStyle() } : undefined} />
        <Tooltip {...tooltipStyle(theme)} />
        <Line type="monotone" dataKey="value" stroke="#06b6d4" strokeWidth={2} dot={{ fill: "#06b6d4", r: 3 }} />
      </LineChart>
    </ResponsiveContainer>
  )
}

function RenderPieChart({ chart, height }: RenderProps) {
  // #807: 自订阅主题 —— 同上。
  const theme = useHudStore((s) => s.theme);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie
          data={chart.data}
          dataKey="value"
          nameKey="name"
          cx="50%"
          cy="50%"
          outerRadius={70}
          label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}
          labelLine={{ stroke: "#94a3b8" }}
          fontSize={13}
        >
          {chart.data.map((_, index) => (
            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip {...tooltipStyle(theme)} />
        <Legend wrapperStyle={{ fontSize: "13px", color: "#94a3b8" }} />
      </PieChart>
    </ResponsiveContainer>
  )
}

function RenderScatterChart({ chart, height }: RenderProps) {
  // #807: 自订阅主题 —— 同上。
  const theme = useHudStore((s) => s.theme);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(6,182,212,0.15)" />
        <XAxis dataKey="x" type="number" tick={tickStyle(theme)} label={chart.x_label ? { value: chart.x_label, position: "insideBottom", offset: -5, fill: "#94a3b8", fontSize: 13 } : undefined} />
        <YAxis dataKey="y" type="number" tick={tickStyle(theme)} label={chart.y_label ? { value: chart.y_label, angle: -90, position: "insideLeft", ...tickStyle() } : undefined} />
        <Tooltip {...tooltipStyle(theme)} />
        <Scatter data={chart.data} fill="#06b6d4" />
      </ScatterChart>
    </ResponsiveContainer>
  )
}

const CHART_RENDERERS: Record<ChartData["type"], React.FC<RenderProps>> = {
  bar: RenderBarChart,
  line: RenderLineChart,
  pie: RenderPieChart,
  scatter: RenderScatterChart,
}

/** 图表类型是否可渲染（bar/line/pie/scatter 单序列契约）。 */
export function isChartTypeSupported(type: string): boolean {
  return type in CHART_RENDERERS;
}

interface ChartCoreProps {
  chart: ChartData;
  /** ResponsiveContainer 高度（px 或 '100%' 填满有界父容器）；缺省 200 与 chat 内嵌图表一致。 */
  height?: number | `${number}%`;
}

/** 共用图表渲染核（无外层卡片/标题 —— 由调用方按自己的 chrome 组合）。 */
export function ChartCore({ chart, height = 200 }: ChartCoreProps) {
  const Renderer = CHART_RENDERERS[chart.type]
  if (!Renderer) return null
  return <Renderer chart={chart} height={height} />
}
