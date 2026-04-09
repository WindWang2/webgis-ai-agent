# Chat Chart Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add inline chart rendering in chat messages via a `generate_chart` tool that returns structured data, rendered by Recharts in the frontend.

**Architecture:** Backend adds a thin `generate_chart` tool that validates params and returns a standardized `{ chart: {...} }` JSON. Frontend adds a `ChartRenderer` component using Recharts, and `chat-panel.tsx` detects chart data in tool results to render charts inline below message text.

**Tech Stack:** Python (FastAPI tool), Recharts (React charting), existing SSE tool_call/tool_result pipeline.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `app/tools/chart.py` | Create | `generate_chart` tool function + `register_chart_tools` |
| `app/api/routes/chat.py` | Modify (line 14, 24) | Import and call `register_chart_tools` |
| `app/services/chat_engine.py` | Modify (line 233-264) | Add chart guidance to SYSTEM_PROMPT |
| `frontend/components/chat/chart-renderer.tsx` | Create | Recharts-based chart rendering component |
| `frontend/components/chat/chat-panel.tsx` | Modify | Add `charts` to Message, detect chart in tool_result, render ChartRenderer |
| `tests/test_chart_tool.py` | Create | Tests for generate_chart tool |

---

### Task 1: Backend — `generate_chart` tool

**Files:**
- Create: `app/tools/chart.py`
- Create: `tests/test_chart_tool.py`

- [ ] **Step 1: Write failing tests for generate_chart**

Create `tests/test_chart_tool.py`:

```python
"""Tests for generate_chart tool"""
import json
import pytest
from app.tools.chart import generate_chart


def test_bar_chart():
    data = json.dumps([{"name": "海淀", "value": 45}, {"name": "朝阳", "value": 38}])
    result = generate_chart(chart_type="bar", title="学校数量", data=data)
    assert "chart" in result
    chart = result["chart"]
    assert chart["type"] == "bar"
    assert chart["title"] == "学校数量"
    assert len(chart["data"]) == 2
    assert chart["data"][0]["name"] == "海淀"
    assert chart["data"][0]["value"] == 45


def test_pie_chart():
    data = json.dumps([{"name": "学校", "value": 30}, {"name": "医院", "value": 20}])
    result = generate_chart(chart_type="pie", title="POI分布", data=data)
    assert result["chart"]["type"] == "pie"
    assert len(result["chart"]["data"]) == 2


def test_scatter_chart():
    data = json.dumps([{"name": "A", "x": 1.5, "y": 3.2}, {"name": "B", "x": 2.1, "y": 4.8}])
    result = generate_chart(chart_type="scatter", title="分布", data=data)
    assert result["chart"]["type"] == "scatter"
    assert result["chart"]["data"][0]["x"] == 1.5


def test_line_chart():
    data = json.dumps([{"name": "1月", "value": 10}, {"name": "2月", "value": 20}])
    result = generate_chart(chart_type="line", title="趋势", data=data)
    assert result["chart"]["type"] == "line"


def test_optional_labels():
    data = json.dumps([{"name": "A", "value": 1}])
    result = generate_chart(chart_type="bar", title="T", data=data, x_label="X轴", y_label="Y轴")
    assert result["chart"]["x_label"] == "X轴"
    assert result["chart"]["y_label"] == "Y轴"


def test_invalid_chart_type():
    data = json.dumps([{"name": "A", "value": 1}])
    result = generate_chart(chart_type="radar", title="T", data=data)
    assert "error" in result


def test_invalid_data_json():
    result = generate_chart(chart_type="bar", title="T", data="not json")
    assert "error" in result


def test_empty_data():
    result = generate_chart(chart_type="bar", title="T", data="[]")
    assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_chart_tool.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.tools.chart'`

- [ ] **Step 3: Implement generate_chart tool**

Create `app/tools/chart.py`:

```python
"""图表生成 FC 工具"""
import json
import logging
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

VALID_CHART_TYPES = {"bar", "line", "pie", "scatter"}


def generate_chart(chart_type: str, title: str, data: str, x_label: str = "", y_label: str = "") -> dict:
    """生成图表配置数据，供前端渲染"""
    if chart_type not in VALID_CHART_TYPES:
        return {"error": f"Invalid chart_type '{chart_type}'. Must be one of: {', '.join(sorted(VALID_CHART_TYPES))}"}

    try:
        parsed_data = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return {"error": "Invalid data: must be a valid JSON array string"}

    if not isinstance(parsed_data, list) or len(parsed_data) == 0:
        return {"error": "data must be a non-empty JSON array"}

    chart = {
        "type": chart_type,
        "title": title,
        "data": parsed_data,
    }
    if x_label:
        chart["x_label"] = x_label
    if y_label:
        chart["y_label"] = y_label

    return {"chart": chart}


def register_chart_tools(registry: ToolRegistry):
    """注册图表工具"""
    registry.register(
        name="generate_chart",
        description="生成统计图表（柱状图/折线图/饼图/散点图）。先用查询工具获取数据，再调用此工具将结果可视化。",
        func=generate_chart,
        param_descriptions={
            "chart_type": '图表类型: "bar"(柱状图), "line"(折线图), "pie"(饼图), "scatter"(散点图)',
            "title": "图表标题",
            "data": 'JSON数组字符串。柱状/折线/饼图: [{"name":"类别","value":数值}]，散点图: [{"name":"标签","x":数值,"y":数值}]',
            "x_label": "X轴标签（可选）",
            "y_label": "Y轴标签（可选）",
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_chart_tool.py -v`

Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/tools/chart.py tests/test_chart_tool.py
git commit -m "feat: add generate_chart tool with tests"
```

---

### Task 2: Register chart tool and update system prompt

**Files:**
- Modify: `app/api/routes/chat.py` (lines 14, 24)
- Modify: `app/services/chat_engine.py` (lines 233-264, SYSTEM_PROMPT)

- [ ] **Step 1: Register chart tool in chat route**

In `app/api/routes/chat.py`, add import after line 14:

```python
from app.tools.chart import register_chart_tools
```

Add registration after line 24 (after `register_rs_tools(registry)`):

```python
register_chart_tools(registry)
```

- [ ] **Step 2: Update SYSTEM_PROMPT in chat_engine.py**

In `app/services/chat_engine.py`, append to `SYSTEM_PROMPT` (before the closing `"""`), after the existing "重要规则" section:

```python
### 统计图表
当分析结果包含分类统计数据时，主动使用 `generate_chart` 生成可视化图表：
- 分类对比 → chart_type="bar"（如各区POI数量）
- 趋势变化 → chart_type="line"（如时间序列数据）
- 占比分布 → chart_type="pie"（如各类型POI比例）
- 相关性/分布 → chart_type="scatter"（如面积与数量关系）

data 参数为 JSON 字符串，格式：[{"name": "类别", "value": 数值}]
散点图格式：[{"name": "标签", "x": 数值, "y": 数值}]

注意：先调用查询工具获取数据，再调用 generate_chart 生成图表。
```

- [ ] **Step 3: Verify tool registration**

Run: `cd /home/kevin/project/webgis-ai-agent && python -c "from app.tools.registry import ToolRegistry; from app.tools.chart import register_chart_tools; r = ToolRegistry(); register_chart_tools(r); print([s['function']['name'] for s in r.get_schemas()])"`

Expected: `['generate_chart']`

- [ ] **Step 4: Commit**

```bash
git add app/api/routes/chat.py app/services/chat_engine.py
git commit -m "feat: register chart tool and add chart guidance to system prompt"
```

---

### Task 3: Install Recharts dependency

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install recharts**

Run: `cd /home/kevin/project/webgis-ai-agent/frontend && npm install recharts`

- [ ] **Step 2: Verify installation**

Run: `cd /home/kevin/project/webgis-ai-agent/frontend && node -e "require('recharts'); console.log('recharts OK')"`

Expected: `recharts OK`

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "feat: add recharts dependency"
```

---

### Task 4: Frontend — ChartRenderer component

**Files:**
- Create: `frontend/components/chat/chart-renderer.tsx`

- [ ] **Step 1: Create ChartRenderer component**

Create `frontend/components/chat/chart-renderer.tsx`:

```tsx
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
          label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
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
```

- [ ] **Step 2: Verify it compiles**

Run: `cd /home/kevin/project/webgis-ai-agent/frontend && npx tsc --noEmit --pretty 2>&1 | head -20`

Expected: No errors related to chart-renderer.tsx (existing errors elsewhere are OK)

- [ ] **Step 3: Commit**

```bash
git add frontend/components/chat/chart-renderer.tsx
git commit -m "feat: add ChartRenderer component with Recharts"
```

---

### Task 5: Frontend — Integrate chart rendering in chat-panel

**Files:**
- Modify: `frontend/components/chat/chat-panel.tsx`

- [ ] **Step 1: Add import for ChartRenderer**

In `chat-panel.tsx`, add after the `import remarkGfm` line (line 8):

```typescript
import { ChartRenderer, ChartData } from "@/components/chat/chart-renderer"
```

- [ ] **Step 2: Add `charts` field to Message interface**

In `chat-panel.tsx`, modify the `Message` interface (around line 10-16) to add `charts`:

```typescript
interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  timestamp: Date
  isThinking?: boolean
  charts?: ChartData[]
}
```

- [ ] **Step 3: Handle chart data in tool_result event**

In the `handleSend` function, find the `tool_result` event handler block (around line 191-220). Add chart detection at the start of that block, right after `const toolResult = ...` (line 193):

Replace the entire `} else if (eventType === "tool_result") {` block (lines 191-220) with:

```typescript
        } else if (eventType === "tool_result") {
          const toolName = typeof data === "object" ? data.name || "unknown" : "unknown"
          const toolResult = typeof data === "object" ? (data.result || data) : data
          console.log("[ChatPanel] tool_result:", toolName, "hasGeojson:", !!toolResult?.geojson, "hasChart:", !!toolResult?.chart, "features:", toolResult?.geojson?.features?.length)

          // 通知父组件渲染 GeoJSON
          if (onToolResult) {
            onToolResult(toolName, toolResult)
          }

          // 检测图表数据
          if (toolResult?.chart) {
            setMessages(prev => prev.map(msg =>
              msg.id === thinkingMessage.id
                ? { ...msg, charts: [...(msg.charts || []), toolResult.chart as ChartData], isThinking: false }
                : msg
            ))
          }

          // 简化显示
          let summary = ""
          if (toolResult?.chart) {
            summary = `图表已生成: ${toolResult.chart.title}`
          } else if (toolResult?.count !== undefined) {
            summary = `找到 ${toolResult.count} 个结果`
          } else if (toolResult?.stats) {
            summary = `统计完成`
          } else if (toolResult?.error) {
            summary = `错误: ${toolResult.error}`
          } else if (toolResult?.status === "ok") {
            summary = `数据获取成功`
          } else {
            summary = "完成"
          }

          assistantContent += `\n✅ **${toolName}**: ${summary}\n`
          setMessages(prev => prev.map(msg =>
            msg.id === thinkingMessage.id
              ? { ...msg, content: assistantContent, isThinking: false }
              : msg
          ))
          scrollToBottom()
```

- [ ] **Step 4: Also handle chart in step_result event**

Find the `step_result` handler (around line 169-172). After the existing `if (data.has_geojson && onToolResult)` block, add chart detection:

```typescript
        } else if (eventType === "step_result" && data?.task_id) {
          handleStepResult(data.task_id, data.step_id, data.tool, data.result, data.has_geojson)
          if (data.has_geojson && onToolResult) {
            onToolResult(data.tool, data.result)
          }
          // 检测图表数据
          if (data.result?.chart) {
            setMessages(prev => prev.map(msg =>
              msg.id === thinkingMessage.id
                ? { ...msg, charts: [...(msg.charts || []), data.result.chart as ChartData], isThinking: false }
                : msg
            ))
          }
```

- [ ] **Step 5: Render charts in message bubble**

Find the message rendering section (around line 330-336) where ReactMarkdown is rendered. Replace:

```tsx
                <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-headings:my-2 prose-pre:my-1 prose-code:text-xs break-words">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {message.content}
                  </ReactMarkdown>
                </div>
```

With:

```tsx
                <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-headings:my-2 prose-pre:my-1 prose-code:text-xs break-words">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {message.content}
                  </ReactMarkdown>
                  {message.charts?.map((chart, idx) => (
                    <ChartRenderer key={`chart-${message.id}-${idx}`} chart={chart} />
                  ))}
                </div>
```

- [ ] **Step 6: Verify frontend compiles**

Run: `cd /home/kevin/project/webgis-ai-agent/frontend && npx tsc --noEmit --pretty 2>&1 | head -20`

Expected: No new errors related to chat-panel.tsx

- [ ] **Step 7: Commit**

```bash
git add frontend/components/chat/chat-panel.tsx
git commit -m "feat: integrate chart rendering in chat panel"
```

---

### Task 6: Smoke test — end to end

- [ ] **Step 1: Run backend tests**

Run: `cd /home/kevin/project/webgis-ai-agent && python -m pytest tests/test_chart_tool.py -v`

Expected: All 8 tests PASS

- [ ] **Step 2: Verify backend starts**

Run: `cd /home/kevin/project/webgis-ai-agent && timeout 5 python -c "from app.api.routes.chat import registry; tools = registry.list_tools(); assert 'generate_chart' in tools; print('Tools:', tools)"`

Expected: Output includes `generate_chart` in the tools list

- [ ] **Step 3: Verify frontend builds**

Run: `cd /home/kevin/project/webgis-ai-agent/frontend && npx next build 2>&1 | tail -10`

Expected: Build succeeds without errors

- [ ] **Step 4: Commit if any fixes were needed**

Only if fixes were applied in steps 1-3:

```bash
git add -A
git commit -m "fix: resolve integration issues from smoke test"
```
