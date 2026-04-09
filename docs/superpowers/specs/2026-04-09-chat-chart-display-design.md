# Chat Chart Display Design

> 在对话中内联显示二维统计图表，AI 通过 tool calling 主动生成图表数据，前端用 Recharts 渲染。

## 决策摘要

| 决策项 | 选择 |
|--------|------|
| 数据来源 | Tool calling（`generate_chart` 工具） |
| 图表类型 | 柱状图、折线图、饼图、散点图 |
| 渲染库 | Recharts |
| 工具设计 | 单一 `generate_chart` 工具，`chart_type` 参数区分 |
| 显示方式 | 内联消息气泡 + hover tooltip |
| 触发方式 | AI 自主判断（system prompt 引导） |
| 地图联动 | GIS 数据走现有 geojson 图层路径，图表走聊天内联路径，天然联动 |

## 1. 后端：`generate_chart` 工具

### 1.1 新增 `app/tools/chart.py`

注册一个 `generate_chart` 工具到 `ToolRegistry`。

**参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `chart_type` | string | 是 | `"bar"` / `"line"` / `"pie"` / `"scatter"` |
| `title` | string | 是 | 图表标题 |
| `x_label` | string | 否 | X 轴标签（饼图不需要） |
| `y_label` | string | 否 | Y 轴标签 |
| `data` | string | 是 | JSON 字符串，数据点数组 |

**data 格式**：

- bar / line / pie: `[{"name": "类别A", "value": 100}, {"name": "类别B", "value": 200}]`
- scatter: `[{"name": "标签A", "x": 1.5, "y": 3.2}, {"name": "标签B", "x": 2.1, "y": 4.8}]`

**返回值**：

```json
{
  "chart": {
    "type": "bar",
    "title": "北京各区学校数量",
    "x_label": "区",
    "y_label": "数量",
    "data": [{"name": "海淀", "value": 45}, {"name": "朝阳", "value": 38}]
  }
}
```

工具逻辑很薄：验证 `chart_type` 合法、解析 `data` JSON 字符串、组装标准化返回。

### 1.2 工具注册

在 `app/tools/__init__.py` 中注册 `generate_chart` 到 `ToolRegistry`。

## 2. System Prompt 更新

在 `app/services/chat_engine.py` 的 `SYSTEM_PROMPT` 中新增图表工具使用指引：

```
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

## 3. 前端：图表渲染

### 3.1 新增 `frontend/components/chat/chart-renderer.tsx`

**ChartData 类型**：

```typescript
interface ChartData {
  type: "bar" | "line" | "pie" | "scatter"
  title: string
  x_label?: string
  y_label?: string
  data: ChartDataPoint[]
}

interface ChartDataPoint {
  name: string
  value?: number    // bar, line, pie
  x?: number        // scatter
  y?: number        // scatter
}
```

**组件行为**：

- 接收 `ChartData` 对象
- 根据 `type` 渲染对应 Recharts 组件：`BarChart` / `LineChart` / `PieChart` / `ScatterChart`
- 统一支持 `Tooltip` 组件（hover 显示数值）
- 宽度 100%（`ResponsiveContainer`），高度 200px
- 样式匹配现有聊天面板深色主题（cyan/dark 配色）

### 3.2 修改 `frontend/components/chat/chat-panel.tsx`

**Message 接口扩展**：

```typescript
interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  timestamp: Date
  isThinking?: boolean
  charts?: ChartData[]  // 新增：该消息关联的图表
}
```

**tool_result 处理逻辑修改**：

在处理 `tool_result` / `step_result` 事件时：

1. 检查 `result` 中是否包含 `chart` 字段
2. 有 `chart` → 将图表数据追加到当前 assistant 消息的 `charts` 数组
3. 有 `geojson` → 继续通过 `onToolResult` 通知地图渲染图层（现有逻辑不变）
4. 两者可同时存在

**消息渲染修改**：

在 assistant 消息气泡中，Markdown 内容下方依次渲染 `charts` 数组中的每个 `ChartRenderer`。

### 3.3 依赖安装

`frontend/package.json` 添加 `recharts` 依赖。

## 4. 地图联动

无需额外联动机制。AI 的 ReAct 循环依次调用多个工具：

```
用户: "分析北京各区学校分布"
→ AI 调用 query_osm_poi → tool_result 含 geojson → 地图渲染 POI 图层
→ AI 调用 generate_chart → tool_result 含 chart → 聊天渲染柱状图
→ AI 输出文字总结
```

SSE 事件流已支持多轮 tool_call，地图和图表各走各的渲染路径。

## 5. 文件变动清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/tools/chart.py` | 新增 | `generate_chart` 工具实现 |
| `app/tools/__init__.py` | 修改 | 注册图表工具 |
| `app/services/chat_engine.py` | 修改 | SYSTEM_PROMPT 增加图表指引 |
| `frontend/components/chat/chart-renderer.tsx` | 新增 | Recharts 图表渲染组件 |
| `frontend/components/chat/chat-panel.tsx` | 修改 | tool_result 中识别 chart 字段，Message 接口扩展 charts 字段，渲染 ChartRenderer |
| `frontend/package.json` | 修改 | 添加 `recharts` 依赖 |

## 6. 不变的部分

- SSE 事件协议不变，复用现有 `tool_call` / `tool_result` / `step_result` 事件
- 地图渲染逻辑不变，geojson 走现有 `onToolResult` 路径
- `TaskTracker` 不变，`generate_chart` 作为普通 step 被跟踪
- `ToolRegistry` 接口不变，图表工具用现有 `register()` 方法注册
