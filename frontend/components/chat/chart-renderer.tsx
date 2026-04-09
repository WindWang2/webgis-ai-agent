"use client"

import {
  BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend,
} from "recharts"

export interface ChartDataPoint {
  name: string
  value?: number
  x?: number
  y?: number
}

export interface ChartData {
  type: "bar" | "line" | "pie" | "scatter"
  title: string
  x_label?: string
  y_label?: string
  data: ChartDataPoint[]
}

const COLORS = [
  "#06b6d4", "#22d3ee", "#67e8f9", "#a5f3fc",
  "#0891b2", "#0e7490", "#155e75", "#164e63",
]

const TOOLTIP_STYLE = {
  contentStyle: {
    backgroundColor: "#0c1e2e",
    border: "1px solid rgba(6,182,212,0.3)",
    borderRadius: "6px",
    color: "#e2e8f0",
    fontSize: "12px",
  },
}

function RenderBarChart({ chart }: { chart: ChartData }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={chart.data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(6,182,212,0.15)" />
        <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} />
        <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} label={chart.y_label ? { value: chart.y_label, angle: -90, position: "insideLeft", fill: "#94a3b8", fontSize: 11 } : undefined} />
        <Tooltip {...TOOLTIP_STYLE} />
        <Bar dataKey="value" fill="#06b6d4" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function RenderLineChart({ chart }: { chart: ChartData }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={chart.data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(6,182,212,0.15)" />
        <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} />
        <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} label={chart.y_label ? { value: chart.y_label, angle: -90, position: "insideLeft", fill: "#94a3b8", fontSize: 11 } : undefined} />
        <Tooltip {...TOOLTIP_STYLE} />
        <Line type="monotone" dataKey="value" stroke="#06b6d4" strokeWidth={2} dot={{ fill: "#06b6d4", r: 3 }} />
      </LineChart>
    </ResponsiveContainer>
  )
}

function RenderPieChart({ chart }: { chart: ChartData }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
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
          fontSize={11}
        >
          {chart.data.map((_, index) => (
            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip {...TOOLTIP_STYLE} />
        <Legend wrapperStyle={{ fontSize: "11px", color: "#94a3b8" }} />
      </PieChart>
    </ResponsiveContainer>
  )
}

function RenderScatterChart({ chart }: { chart: ChartData }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <ScatterChart margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(6,182,212,0.15)" />
        <XAxis dataKey="x" type="number" tick={{ fill: "#94a3b8", fontSize: 11 }} label={chart.x_label ? { value: chart.x_label, position: "insideBottom", offset: -5, fill: "#94a3b8", fontSize: 11 } : undefined} />
        <YAxis dataKey="y" type="number" tick={{ fill: "#94a3b8", fontSize: 11 }} label={chart.y_label ? { value: chart.y_label, angle: -90, position: "insideLeft", fill: "#94a3b8", fontSize: 11 } : undefined} />
        <Tooltip {...TOOLTIP_STYLE} />
        <Scatter data={chart.data} fill="#06b6d4" />
      </ScatterChart>
    </ResponsiveContainer>
  )
}

const CHART_RENDERERS: Record<ChartData["type"], React.FC<{ chart: ChartData }>> = {
  bar: RenderBarChart,
  line: RenderLineChart,
  pie: RenderPieChart,
  scatter: RenderScatterChart,
}

export function ChartRenderer({ chart }: { chart: ChartData }) {
  const Renderer = CHART_RENDERERS[chart.type]
  if (!Renderer) return null

  return (
    <div className="mt-2 rounded-lg border border-cyan-500/20 bg-cyan-950/30 p-3">
      <h4 className="mb-2 text-xs font-medium text-cyan-300">{chart.title}</h4>
      <Renderer chart={chart} />
    </div>
  )
}
