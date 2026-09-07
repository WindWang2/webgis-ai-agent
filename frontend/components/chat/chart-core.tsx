"use client"

/**
 * ChartCore —— Recharts 渲染核（#D2 图表协议复用）。
 *
 * V4（Design System）：图表 kind 词表扩至 18 种 native kind
 * （app/lib/cartography/chart_kinds.py 单一权威；violin planned 不渲染）。
 * - recharts 族：bar/hbar/grouped/stacked/line/area/scatter/histogram/
 *   pie/donut/radar/rose/timeseries/cumulative
 * - 自绘 SVG 族（recharts 无原生支持，诚实自绘）：box_plot（五数概括）/
 *   heat_matrix（行×列色阵）/ kpi_card / ranking_list
 *
 * 从 chat 消息与地图 chart_panel 共用同一套图表 schema/主题，不出现
 * 第二套图表实现。chart-renderer.tsx 只是薄壳（卡片 + 标题）。
 */

import {
  BarChart, Bar, LineChart, Line, AreaChart, Area, PieChart, Pie, Cell,
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend,
  RadialBarChart, RadialBar,
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
} from "recharts"

import type { ChartData, ChartDataPoint } from "@/lib/types"
import { useHudStore } from "@/lib/store/useHudStore"

const COLORS = [
  "#06b6d4", "#22d3ee", "#67e8f9", "#a5f3fc",
  "#0891b2", "#0e7490", "#155e75", "#164e63",
]

// recharts v3 类型怪癖：极轴组件返回 ReactNode，自定义 tick 对象下
// TS2786 误报 —— 渲染层用宽松 JSX 别名（运行时行为不变）。
const PolarAngleAxisJsx = PolarAngleAxis as unknown as React.FC<Record<string, unknown>>;
const PolarRadiusAxisJsx = PolarRadiusAxis as unknown as React.FC<Record<string, unknown>>;

// V4：heat_matrix 固定 Blues 色阶（与 palette 家族同源的展示用 ramp）
const HEAT_RAMP = ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"]

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

/** 类别高亮：命中 accent 实色，未命中降透明度（视觉对比而非隐藏）。 */
const HIGHLIGHT_FILL = "#06b6d4";
const HIGHLIGHT_DIM = "rgba(6,182,212,0.25)";

function isHighlighted(name: unknown, highlighted: string[] | undefined): boolean {
  return !!highlighted && highlighted.length > 0 && highlighted.includes(String(name ?? ""));
}

interface RenderProps {
  chart: ChartData;
  height: number | `${number}%`;
  /** Workspace V2（Goal D4）：高亮类别集合（map→chart 方向）。 */
  highlightedCategories?: string[];
  /** Workspace V2（Goal D3）：类别点击（chart→map 方向；bar/pie 支持）。 */
  onSelectCategory?: ((name: string) => void) | null;
}

/** 多序列 → recharts tidy 行（name 列 + 每序列一列）。 */
function tidySeries(chart: ChartData): Record<string, unknown>[] {
  const rows: Record<string, unknown>[] = []
  const byName = new Map<string, Record<string, unknown>>()
  for (const ser of chart.series ?? []) {
    for (const pt of ser.data) {
      let row = byName.get(pt.name)
      if (!row) {
        row = { name: pt.name }
        byName.set(pt.name, row)
        rows.push(row)
      }
      row[ser.name] = pt.value ?? 0
    }
  }
  return rows
}

/** 从 recharts onClick 状态中容错提取类别名（Bar/Pie/RadialBar 形态族）。 */
function extractClickedName(state: unknown): unknown {
  const s = state as { payload?: { name?: unknown }; activePayload?: Array<{ payload?: { name?: unknown } }> } | undefined;
  if (s?.payload?.name != null) return s.payload.name;
  const first = s?.activePayload?.[0]?.payload?.name;
  if (first != null) return first;
  return null;
}

function barClickHandler(onSelectCategory: ((name: string) => void) | null | undefined) {
  return onSelectCategory
    ? { onClick: (state: unknown) => {
        const name = extractClickedName(state);
        if (name != null) onSelectCategory(String(name));
      } }
    : {};
}

function RenderBarChart({ chart, height, highlightedCategories, onSelectCategory }: RenderProps) {
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
        <Bar
          dataKey="value"
          fill="#06b6d4"
          radius={[2, 2, 0, 0]}
          {...barClickHandler(onSelectCategory)}
        >
          {highlightedCategories?.length
            ? chart.data.map((entry, index) => (
                <Cell
                  key={`cell-${index}`}
                  fill={isHighlighted(entry.name, highlightedCategories) ? HIGHLIGHT_FILL : HIGHLIGHT_DIM}
                />
              ))
            : null}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

/** V4：条形图（水平柱 —— 类目名长的排名对比）。 */
function RenderHorizontalBarChart({ chart, height, highlightedCategories, onSelectCategory }: RenderProps) {
  const theme = useHudStore((s) => s.theme);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart layout="vertical" data={chart.data} margin={{ top: 5, right: 20, bottom: 5, left: 20 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(6,182,212,0.15)" />
        <XAxis type="number" tick={tickStyle(theme)} />
        <YAxis type="category" dataKey="name" width={80} tick={tickStyle(theme)} />
        <Tooltip {...tooltipStyle(theme)} />
        <Bar dataKey="value" fill="#06b6d4" radius={[0, 2, 2, 0]} {...barClickHandler(onSelectCategory)}>
          {highlightedCategories?.length
            ? chart.data.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={isHighlighted(entry.name, highlightedCategories) ? HIGHLIGHT_FILL : HIGHLIGHT_DIM} />
              ))
            : null}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

/** V4：分组/堆叠柱状图（多序列；stacked 语义由 chart.stacked 驱动）。 */
function RenderMultiBarChart({ chart, height, onSelectCategory }: RenderProps) {
  const theme = useHudStore((s) => s.theme);
  // data-only 载荷回退为单序列（不画只有坐标轴的空图）
  const series = chart.series?.length
    ? chart.series
    : [{ name: "value", data: chart.data }]
  const rows = tidySeries({ ...chart, series })
  const names = series.map((s) => s.name)
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(6,182,212,0.15)" />
        <XAxis dataKey="name" tick={tickStyle(theme)} />
        <YAxis tick={tickStyle(theme)} label={chart.y_label ? { value: chart.y_label, angle: -90, position: "insideLeft", ...tickStyle() } : undefined} />
        <Tooltip {...tooltipStyle(theme)} />
        <Legend wrapperStyle={{ fontSize: "13px" }} />
        {names.map((n, i) => (
          <Bar
            key={n}
            dataKey={n}
            fill={COLORS[i % COLORS.length]}
            stackId={chart.stacked ? "stack" : undefined}
            radius={chart.stacked ? [0, 0, 0, 0] : [2, 2, 0, 0]}
            {...barClickHandler(onSelectCategory)}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

function RenderLineChart({ chart, height }: RenderProps) {
  // 类别高亮/选择不适用于连续序列图（见 ChartCore 注释）。
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

/** V4：面积图 / 累计曲线（cumulative 由调用方预衍生，此处统一画面积）。 */
function RenderAreaChart({ chart, height }: RenderProps) {
  const theme = useHudStore((s) => s.theme);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={chart.data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(6,182,212,0.15)" />
        <XAxis dataKey="name" tick={tickStyle(theme)} />
        <YAxis tick={tickStyle(theme)} label={chart.y_label ? { value: chart.y_label, angle: -90, position: "insideLeft", ...tickStyle() } : undefined} />
        <Tooltip {...tooltipStyle(theme)} />
        <Area type="monotone" dataKey="value" stroke="#06b6d4" fill="rgba(6,182,212,0.25)" strokeWidth={2} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

/** V4：直方图（分箱在数据提交前由后端 classify 完成 —— 与地图分级同断点）。 */
function RenderHistogramChart({ chart, height, onSelectCategory }: RenderProps) {
  const theme = useHudStore((s) => s.theme);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={chart.data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }} barCategoryGap="0%">
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(6,182,212,0.15)" />
        <XAxis dataKey="name" tick={tickStyle(theme)} label={chart.x_label ? { value: chart.x_label, position: "insideBottom", offset: -5, fill: "var(--text-muted)", fontSize: 13 } : undefined} />
        <YAxis tick={tickStyle(theme)} />
        <Tooltip {...tooltipStyle(theme)} />
        {/* Workbench V4（Wave 5）：bin 是离散类别（区间名），点击发布类别选择。 */}
        <Bar dataKey="value" fill="#0891b2" radius={[1, 1, 0, 0]} {...barClickHandler(onSelectCategory)} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function RenderPieChart({ chart, height, highlightedCategories, onSelectCategory, donut }: RenderProps & { donut?: boolean }) {
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
          innerRadius={donut ? 42 : 0}
          label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}
          labelLine={{ stroke: "var(--text-muted)" }}
          fontSize={13}
          {...(onSelectCategory
            ? { onClick: (state: unknown) => {
                const name = (state as { name?: unknown } | undefined)?.name
                  ?? (state as { payload?: { name?: unknown } } | undefined)?.payload?.name;
                if (name != null) onSelectCategory(String(name));
              } }
            : {})}
        >
          {chart.data.map((entry, index) => (
            <Cell
              key={`cell-${index}`}
              fill={
                highlightedCategories?.length && !isHighlighted(entry.name, highlightedCategories)
                  ? HIGHLIGHT_DIM
                  : COLORS[index % COLORS.length]
              }
            />
          ))}
        </Pie>
        <Tooltip {...tooltipStyle(theme)} />
        <Legend wrapperStyle={{ fontSize: "13px", color: "var(--text-muted)" }} />
      </PieChart>
    </ResponsiveContainer>
  )
}

function RenderScatterChart({ chart, height }: RenderProps) {
  // 同上：散点无类别轴。
  // #807: 自订阅主题 —— 同上。
  const theme = useHudStore((s) => s.theme);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ScatterChart margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(6,182,212,0.15)" />
        <XAxis dataKey="x" type="number" tick={tickStyle(theme)} label={chart.x_label ? { value: chart.x_label, position: "insideBottom", offset: -5, fill: "var(--text-muted)", fontSize: 13 } : undefined} />
        <YAxis dataKey="y" type="number" tick={tickStyle(theme)} label={chart.y_label ? { value: chart.y_label, angle: -90, position: "insideLeft", ...tickStyle() } : undefined} />
        <Tooltip {...tooltipStyle(theme)} />
        <Scatter data={chart.data} fill="#06b6d4" />
      </ScatterChart>
    </ResponsiveContainer>
  )
}

/** V4：雷达图（多维指标对比；多序列支持）。 */
function RenderRadarChart({ chart, height }: RenderProps) {
  const theme = useHudStore((s) => s.theme);
  const rows = chart.series?.length
    ? tidySeries(chart)
    : chart.data.map((p) => ({ name: p.name, value: p.value ?? 0 }))
  const names = chart.series?.length ? chart.series.map((s) => s.name) : ["value"]
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RadarChart data={rows} cx="50%" cy="50%" outerRadius="70%">
        <PolarGrid stroke="rgba(100,116,139,0.3)" />
        <PolarAngleAxisJsx dataKey="name" tick={tickStyle(theme)} />
        <PolarRadiusAxisJsx tick={tickStyle(theme)} />
        {names.map((n, i) => (
          <Radar key={n} dataKey={n} stroke={COLORS[i % COLORS.length]} fill={COLORS[i % COLORS.length]} fillOpacity={0.35} />
        ))}
        <Tooltip {...tooltipStyle(theme)} />
      </RadarChart>
    </ResponsiveContainer>
  )
}

/** V4：玫瑰图（极区柱 —— RadialBar 近似；角度按值等分）。 */
function RenderRoseChart({ chart, height, onSelectCategory }: RenderProps) {
  const theme = useHudStore((s) => s.theme);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RadialBarChart
        data={chart.data}
        innerRadius="25%"
        outerRadius="85%"
        {...(onSelectCategory
          ? { onClick: (state: unknown) => {
              const name = extractClickedName(state);
              if (name != null) onSelectCategory(String(name));
            } }
          : {})}
      >
        <PolarAngleAxisJsx type="category" dataKey="name" tick={tickStyle(theme)} />
        <RadialBar dataKey="value" background fill="#06b6d4" />
        <Tooltip {...tooltipStyle(theme)} />
      </RadialBarChart>
    </ResponsiveContainer>
  )
}

// ── 自绘 SVG 族（recharts 无原生支持 —— 诚实自绘，不伪装）──────────────

function boxScales(points: ChartDataPoint[]) {
  const nums = points.flatMap((p) => [p.min, p.q1, p.value, p.q3, p.max]).filter(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  );
  if (!nums.length) return { lo: 0, hi: 1 };
  return { lo: Math.min(...nums), hi: Math.max(...nums) };
}

/** V4：箱线图（五数概括；recharts 无原生箱线 —— 自绘 SVG）。 */
function RenderBoxPlot({ chart, height }: RenderProps) {
  const W = 420;
  const H = typeof height === "number" ? height : 200;
  const padL = 16, padR = 16, padT = 14, padB = 30;
  const { lo, hi } = boxScales(chart.data);
  const span = hi - lo || 1;
  const y = (v: number) => padT + (1 - (v - lo) / span) * (H - padT - padB);
  const slot = (W - padL - padR) / Math.max(1, chart.data.length);
  const bw = Math.min(48, slot * 0.55);
  const fmt = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(1));
  return (
    <svg role="img" aria-label={`箱线图 ${chart.title}`} width="100%" viewBox={`0 0 ${W} ${H}`} data-testid="chart-box-plot">
      {[lo, (lo + hi) / 2, hi].map((v, i) => (
        <g key={i}>
          <line x1={padL} x2={W - padR} y1={y(v)} y2={y(v)} stroke="rgba(100,116,139,0.25)" strokeDasharray="3 3" />
          <text x={padL - 4} y={y(v) + 3} textAnchor="start" fontSize={9} fill="var(--text-muted)">{fmt(v)}</text>
        </g>
      ))}
      {chart.data.map((p, i) => {
        const cx = padL + slot * i + slot / 2;
        const q1 = typeof p.q1 === "number" ? y(p.q1) : y(p.value ?? lo);
        const q3 = typeof p.q3 === "number" ? y(p.q3) : y(p.value ?? lo);
        const mn = typeof p.min === "number" ? y(p.min) : q1;
        const mx = typeof p.max === "number" ? y(p.max) : q3;
        const med = y(p.value ?? lo);
        return (
          <g key={i}>
            <line x1={cx} x2={cx} y1={mn} y2={mx} stroke="#0891b2" strokeWidth={1} />
            <line x1={cx - bw / 3} x2={cx + bw / 3} y1={mn} y2={mn} stroke="#0891b2" strokeWidth={1} />
            <line x1={cx - bw / 3} x2={cx + bw / 3} y1={mx} y2={mx} stroke="#0891b2" strokeWidth={1} />
            <rect x={cx - bw / 2} y={Math.min(q1, q3)} width={bw} height={Math.max(2, Math.abs(q3 - q1))} rx={2} fill="rgba(6,182,212,0.35)" stroke="#0891b2" />
            <line x1={cx - bw / 2} x2={cx + bw / 2} y1={med} y2={med} stroke="#0e7490" strokeWidth={2} />
            <text x={cx} y={H - 10} textAnchor="middle" fontSize={10} fill="var(--text-muted)">{p.name}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** V4：热矩阵（行×列色阵；series=行、series.data=列）。 */
function RenderHeatMatrix({ chart, height }: RenderProps) {
  const rows = chart.series?.length ? chart.series : [{ name: "", data: chart.data }];
  const W = 460;
  const H = typeof height === "number" ? height : 200;
  const padL = 70, padB = 24;
  const values = rows.flatMap((r) => r.data.map((p) => p.value ?? 0)).filter(Number.isFinite);
  const lo = values.length ? Math.min(...values) : 0;
  const hi = values.length ? Math.max(...values) : 1;
  const span = hi - lo || 1;
  const cellW = (W - padL - 8) / Math.max(1, rows[0]?.data.length ?? 1);
  const rowH = Math.min(30, (H - padB - 8) / Math.max(1, rows.length));
  const colorFor = (v: number) => {
    const t = Math.min(1, Math.max(0, (v - lo) / span));
    return HEAT_RAMP[Math.min(HEAT_RAMP.length - 1, Math.floor(t * HEAT_RAMP.length))];
  };
  return (
    <svg role="img" aria-label={`热矩阵 ${chart.title}`} width="100%" viewBox={`0 0 ${W} ${H}`} data-testid="chart-heat-matrix">
      {rows.map((r, ri) => (
        <g key={ri}>
          <text x={padL - 6} y={8 + ri * rowH + rowH / 2} textAnchor="end" fontSize={10} fill="var(--text-muted)">
            {r.name.length > 10 ? `${r.name.slice(0, 9)}…` : r.name}
          </text>
          {r.data.map((p, ci) => (
            <g key={ci}>
              <rect
                x={padL + ci * cellW} y={8 + ri * rowH}
                width={Math.max(2, cellW - 1)} height={Math.max(2, rowH - 1)}
                fill={colorFor(p.value ?? 0)}
              />
              {cellW > 34 && (
                <text x={padL + ci * cellW + cellW / 2} y={8 + ri * rowH + rowH / 2 + 3} textAnchor="middle" fontSize={9} fill="#1c2733">
                  {p.value ?? ""}
                </text>
              )}
            </g>
          ))}
        </g>
      ))}
      {rows[0]?.data.map((p, ci) => (
        <text key={ci} x={padL + ci * cellW + cellW / 2} y={H - 8} textAnchor="middle" fontSize={9} fill="var(--text-muted)">
          {(p.name ?? "").length > 6 ? `${p.name.slice(0, 5)}…` : p.name}
        </text>
      ))}
    </svg>
  );
}

/** V4：KPI 指标卡（name=指标名，value=主值）。 */
function RenderKpiCards({ chart }: RenderProps) {
  return (
    <div className="grid grid-cols-2 gap-2 p-1" data-testid="chart-kpi-cards">
      {chart.data.slice(0, 4).map((p, i) => (
        <div key={i} className="rounded-chrome border border-edge-subtle bg-surface-raised px-3 py-2">
          <div className="text-micro text-map-chrome-ink-muted">{p.name}</div>
          <div className="text-xl font-semibold tabular-nums text-map-chrome-ink">
            {typeof p.value === "number" ? p.value.toLocaleString() : "—"}
          </div>
        </div>
      ))}
    </div>
  );
}

/** V4：排名列表（值降序 + 比例条）。 */
function RenderRankingList({ chart, highlightedCategories, onSelectCategory }: RenderProps) {
  const rows = [...chart.data].sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  const max = Math.max(...rows.map((r) => r.value ?? 0), 1);
  return (
    <div className="flex flex-col gap-1 p-1" data-testid="chart-ranking-list">
      {rows.slice(0, 10).map((r, i) => {
        const highlighted = isHighlighted(r.name, highlightedCategories);
        return (
          <button
            key={i}
            type="button"
            {...(onSelectCategory ? { onClick: () => onSelectCategory(r.name) } : {})}
            className="flex items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-surface-raised"
          >
            <span className="w-4 text-right text-micro tabular-nums text-map-chrome-ink-muted">{i + 1}</span>
            <span className="w-20 truncate text-micro text-map-chrome-ink">{r.name}</span>
            <span className="h-2 flex-1 rounded-sm bg-edge-subtle">
              <span
                className="block h-2 rounded-sm"
                style={{ width: `${((r.value ?? 0) / max) * 100}%`, background: highlighted ? HIGHLIGHT_FILL : "#06b6d4", opacity: highlighted || !highlightedCategories?.length ? 1 : 0.35 }}
              />
            </span>
            <span className="w-14 text-right text-micro tabular-nums text-map-chrome-ink-muted">{r.value?.toLocaleString()}</span>
          </button>
        );
      })}
    </div>
  );
}

/** V4：累计曲线 —— 前缀和衍生（面积语义，副标题披露累计口径由调用方负责）。 */
function toCumulative(chart: ChartData): ChartData {
  let acc = 0;
  return {
    ...chart,
    data: chart.data.map((p) => {
      acc += p.value ?? 0;
      return { ...p, value: acc };
    }),
  };
}

const CHART_RENDERERS: Partial<Record<ChartData["type"], React.FC<RenderProps>>> = {
  bar: RenderBarChart,
  horizontal_bar: RenderHorizontalBarChart,
  grouped_bar: RenderMultiBarChart,
  stacked_bar: (props) => <RenderMultiBarChart {...props} chart={{ ...props.chart, stacked: true }} />,
  line: RenderLineChart,
  timeseries: RenderLineChart,
  area: RenderAreaChart,
  cumulative: (props) => <RenderAreaChart {...props} chart={toCumulative(props.chart)} />,
  histogram: RenderHistogramChart,
  pie: (props) => <RenderPieChart {...props} />,
  donut: (props) => <RenderPieChart {...props} donut />,
  scatter: RenderScatterChart,
  radar: RenderRadarChart,
  rose: RenderRoseChart,
  box_plot: RenderBoxPlot,
  heat_matrix: RenderHeatMatrix,
  kpi_card: RenderKpiCards,
  ranking_list: RenderRankingList,
}

/** 图表 kind 是否可渲染（chart_kinds 词表的 native 子集）。 */
export function isChartTypeSupported(type: string): boolean {
  return type in CHART_RENDERERS;
}

interface ChartCoreProps {
  chart: ChartData;
  /** ResponsiveContainer 高度（px 或 '100%' 填满有界父容器）；缺省 200 与 chat 内嵌图表一致。 */
  height?: number | `${number}%`;
  /** Workspace V2（Goal D）：类别高亮集合（map→chart）。 */
  highlightedCategories?: string[];
  /** Workspace V2（Goal D）：类别点击回调（chart→map）；仅 bar/pie 支持。 */
  onSelectCategory?: ((name: string) => void) | null;
}

/** 共用图表渲染核（无外层卡片/标题 —— 由调用方按自己的 chrome 组合）。 */
export function ChartCore({ chart, height = 200, highlightedCategories, onSelectCategory }: ChartCoreProps) {
  const Renderer = CHART_RENDERERS[chart.type]
  if (!Renderer) return null
  // 类别选择语义只对离散轴图表成立（line/scatter/area 的 x 是序列/数值域，
  // 点击语义不是类别 —— 如实不支持，不虚构回调）。
  // Workbench V4（Wave 5）：门禁与后端 chart_kinds 的 selectionLinkage 声明
  // 对齐 —— donut/grouped_bar/stacked_bar 渲染器本就带回调（此前被此门禁
  // 拦掉，声明超前于实现）；histogram bin 与 rose 花瓣是离散类别，渲染器
  // 已补点击。scatter 保持不联（数值轴点击不构成类别语义）。
  const CATEGORY_SELECT_KINDS: ReadonlySet<string> = new Set([
    "bar", "pie", "donut", "horizontal_bar", "grouped_bar", "stacked_bar",
    "ranking_list", "histogram", "rose",
  ]);
  const categorySelect = CATEGORY_SELECT_KINDS.has(chart.type) ? onSelectCategory : null;
  return (
    <Renderer
      chart={chart}
      height={height}
      highlightedCategories={highlightedCategories}
      onSelectCategory={categorySelect}
    />
  )
}
