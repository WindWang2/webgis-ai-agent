# 技术规格书：混合能动性画布协同（Mixed-Initiative Canvas Co-Pilot v1）

ADR：`docs/adr/0194-mixed-initiative-canvas-copilot.md`。本文是唯一权威线格式定义；前后端实现与测试以本文为准。

## 1. 数据契约

### 1.1 SpatialAffordanceEnvelope（前端 → 后端）

```jsonc
{
  "envelope_id": "env-01J...",          // 客户端生成，≤64 字符
  "client_generation": 42,              // 可选，单调（跨通道去重辅助）
  "client_ts": 1760000000000,           // epoch ms
  "actions": [ SpatialAffordanceAction ] // 1..12
}
```

### 1.2 SpatialAffordanceAction

| 字段 | 类型 | 约束 |
|---|---|---|
| `action_id` | str | ≤64 字符，客户端生成，跨通道去重键 |
| `kind` | str | 封闭词表：`box_select` \| `freehand_lasso` \| `polygon_lasso` \| `highlight` \| `measure` \| `snap_pick` \| `widget_reply` |
| `geometry` | GeoJSON | `Polygon`（box/lasso/highlight）、`LineString`（measure）、`Point` 或 `MultiPoint`（snap_pick）；`widget_reply` 可省略 |
| `screen_px` | obj | `{x,y,width?,height?}`，int，`0..16384` |
| `layer_refs` | str[] | ≤8，每个 ≤128 字符 |
| `map_view` | obj | `{center:[lng,lat], zoom}`，lng∈[-180,180]，lat∈[-85.05112878,85.05112878]，zoom∈[0,24] |
| `created_at` | float | epoch ms |
| `meta` | obj | kind 专属；`widget_reply` 必含 `{widget_id, kind, value}` |

几何预算：单 action 序列化 ≤ 8KB；环/线顶点 ≤ 256；多环 Polygon ≤ 4 环。信封总序列化 ≤ 64KB。任何 action 违规 → 仅丢弃该 action（`rejected_actions[].reason`）；整体非 dict / actions 空 / 超总预算 → 拒收整封。

### 1.3 摄取结果 CanvasAffordanceAck

```jsonc
{ "accepted": true, "reason": "accepted", "sequence": 17,
  "accepted_actions": ["act-1","act-2"], "rejected_actions": [{"action_id":"act-3","reason":"geometry_invalid"}] }
```

reason 词表：`accepted` / `empty_or_invalid_envelope` / `duplicate` / `ingest_error` / `persist_failed`。

### 1.4 后端持久化形态

`map_state["_situation_canvas_affordances"]`：有界环（尾部 16 条），条目：

```jsonc
{ "sequence": 17, "envelope_id": "...", "action_id": "...", "kind": "box_select",
  "geometry": {...}, "bbox": [w,s,e,n], "layer_refs": [...], "screen_px": {...},
  "observed_at": "2026-09-15T08:00:00Z", "payload_hash": "ab12cd34ef56" }
```

## 2. 空间事实投影（Situation 注入）

### 2.1 SpatialAffordanceToken（服务端内部元语）

`token_id = f"{sequence}:{action_id}"`；字段：`kind / geometry(GeoJSON) / bbox / area_m2? / layer_refs / screen_px / sequence / observed_at / meta`。由 `tokens_from_ring(ring)` 纯函数产出。

### 2.2 布尔拓扑派生 `topology_derive(action_geometry, layer_features, op)`

- 输入：action 几何（WGS84）、图层要素列表 `[{"geometry": GeoJSON, "properties": {...}}]`、`op ∈ {"select_intersect", "difference", "union"}`。
- 语义：`select_intersect` = action 多边形 ∩ 各要素的并（用户"圈选这些要素"）；`difference` = action 多边形 − 要素并（"避开这部分"）；`union` = 命中要素并（"把选中的合成一片"）。
- 输出：`{derived_geometry: GeoJSON|None, hit_count: int, area_m2: float, hit_refs: str[]}`（空交集 → derived_geometry=None, hit_count=0）。
- 实现约束：shapely 纯函数；面积用 geodesic 近似（等面积投影 `EPSG:6933` 或现有项目工具，误差可接受即可）；异常输入（自交、跨反子午线）先 `make_valid`/平滑处理，仍失败返回空结果并带 `error` 字段，不抛出。

### 2.3 情境上下文块 `[画布意图]`

`render_affordance_context_block(ring)` → 有界文本（≤1600 字符），逐行：

```
[画布意图 — 用户在地图上的直接操作，优先级高于文字描述]
- 刚框选范围: W.. S.. E.. N..（多边形, 12 顶点, ≈3.2 km², 图层: water_areas）
- 套索命中: 7 个要素（layer: roads, 排除后剩 5）
- 测量: 1.4 km
- widget 裁决: widget=w-01 (histogram_slider) → breaks=[2.5, 7.0]
```

失败/空环 → 空串（不注入）。`chat.py::_build_situation_env_block` 在结构化情境投影后追加。

### 2.4 SitFact 投影

`affordance_facts(ring)`：每条最近 action 一个 `known` 事实，`source="canvas_affordance"`，`ref=f"canvas:{sequence}:{action_id}"`，value 为紧凑投影（kind/bbox/layer_refs/hit_count 等，≤512B）。

## 3. Generative Widget Protocol（后端 → 前端）

### 3.1 SSE 事件

```
event: ui_action
data: {"type":"ui_action","action":"mount_widget","turn_id":"...","session_id":"...","widget":{...}}
```

非终态事件；`TurnEventBuffer` 记录（resume 重放一致）。前端未知 `ui_action` 一律忽略（向后兼容）。

### 3.2 WidgetSpec

公共字段：`widget_id`（≤64）、`kind`（4 类封闭词表）、`title`（≤200）、`payload`（kind 联合）、`expires_at?`（epoch ms）。

| kind | payload 字段（全部必填除非标注?） |
|---|---|
| `histogram_slider` | `field`≤128；`bins`≤64 项 `{lo:number,hi:number,count:int≥0}`；`breaks`：number[]（升序、落在 [min,max]）；`min`,`max`：number；`unit?`≤16；`layer_ref`≤128 |
| `swipe_compare` | `left`,`right`：`{label≤128, layer_ref≤128}` |
| `candidate_picker` | `candidates`≤12 项 `{id≤64, label≤128, geometry?:GeoJSON≤4KB, stats?:{k≤32: scalar}}`；`selection_mode`: `"single"\|"multi"` |
| `sketch_box` | `prompt`≤300；`baseline_geometry?`：GeoJSON≤4KB；`constraints?`：`{min_area_m2?, max_area_m2?}`；`layer_ref?`≤128 |

预算：payload 序列化 ≤ 16KB。`widget_reply` 回流值形态：histogram_slider → `{breaks:number[]}`；swipe_compare → `{selected:"left"|"right"}`；candidate_picker → `{selected:string[]}`；sketch_box → `{geometry:GeoJSON, area_m2?:number}`。

### 3.3 安全校验（双向）

`validate_widget_spec(spec)`（服务端，pydantic + 递归扫描）与 `isWidgetSpecSafe(json)`（前端，mount 前最后一道）共同规则：

1. 递归遍历 payload：只允许 dict/list/str/int/float/bool/None；其它类型拒收；
2. 键黑名单（大小写不敏感）：`script`、`iframe`、`object`、`embed`、`html`、`innerhtml`、`srcdoc`、任何 `on*` 事件键（`onclick`/`onerror`/…）；
3. 字符串值黑名单：`javascript:` 前缀、`data:text/html`、`<script`、`</script`、`<iframe`（大小写不敏感）；
4. 字段预算（1.2/3.2 全部上限）；
5. 任一命中 → 服务端 ValidationError（widget 不下发、工具结果块剥离并 WARNING 日志）；前端 → 丢弃并 `console.warn` + telemetry 计数。

### 3.4 Tool Pipeline 摘取协议

工具结果载荷（dict）允许携带可选键：

```jsonc
{ ..., "ui_actions": [ {"action": "mount_widget", "widget": {...}} ] }
```

- pipeline 摘取后：合法项挂 `ToolExecutionResult.pending_widgets`（tuple[dict,...]），并从 `llm_payload` 中剥离 `ui_actions` 键（widget 不进 LLM 上下文，避免提示膨胀与自指循环）；
- 非法项：剥离 + WARNING 日志（`[tool_pipeline] invalid widget spec dropped`），不影响工具成败；
- 引擎在 yield `tool_result`（legacy）/`step_result`（Pi mapper）后逐个 yield `ui_action` 事件并 `buffer.record`。

## 4. 前端架构

### 4.1 目录

```
frontend/lib/copilot/affordance.ts        # 类型 + 信封构建 + isWidgetSpecSafe + reportCanvasActions()
frontend/lib/copilot/sketch-capture.ts    # 纯函数：指针序列 → box ring / freehand ring / 顶点吸附
frontend/lib/copilot/widget-binding.ts    # useWidgetReply(widget, onReply) → sendWidgetReply()
frontend/lib/store/slices/copilotSlice.ts # copilotTool/highlight/widgets/reportLatencyMs
frontend/components/copilot/spatial-sketch-tool.tsx
frontend/components/copilot/generative-widgets/widget-host.tsx
frontend/components/copilot/generative-widgets/histogram-slider.tsx
frontend/components/copilot/generative-widgets/swipe-compare.tsx
frontend/components/copilot/generative-widgets/candidate-picker.tsx
frontend/components/copilot/generative-widgets/sketch-box.tsx
```

### 4.2 交互流

1. **激活**：`copilotSlice.setCopilotTool("box_select"|"freehand_lasso"|"polygon_lasso"|null)`；sketch-box widget 的 CTA 亦经此激活。
2. **捕获**：`SpatialSketchTool` SVG 覆层收集指针序列（down/move/up）；`sketch-capture.ts` 产出几何；多边形套索对 sketch store 既有要素顶点做 `snapToVertex`（阈值 12px，`meta.snap_targets` 记录吸附源）。
3. **高亮**：完成即 `setCopilotHighlight({ring, kind})`，SVG `<polygon stroke-dasharray="6 4">` 渲染虚线框；再次激活同工具或 Escape 清除。
4. **上报**：`reportCanvasActions(sessionId, envelope)` 立即 `POST /api/v1/chat/sessions/{id}/canvas-actions`（fire-and-forget，`performance.now()` 差值记入 `reportLatencyMs`）；同时信封随下一次 `streamChat` 请求体的 `canvas_actions` 捎带（服务端内容寻重防双计）。**上报路径同步部分（构建信封 + dispatch + fetch 发起）预算 < 100ms。**
5. **widget 挂载**：SSE `ui_action` → `isWidgetSpecSafe` → `copilotSlice.pushWidget` → `WidgetHost` 渲染；用户操作 → `useWidgetReply` 构造 `widget_reply` action 走第 4 步同通道回流 + `onReply` 本地回调（双向绑定）。

### 4.3 状态切片 copilotSlice

```ts
copilotTool: "box_select"|"freehand_lasso"|"polygon_lasso"|null
copilotHighlight: { ring: [number,number][], kind: string } | null
widgets: WidgetMountPayload[]            // ≤4，FIFO 淘汰
reportLatencyMs: number | null
setCopilotTool / setCopilotHighlight / clearCopilotHighlight /
pushWidget / dismissWidget / recordReportLatency / clearCopilotState
```

## 5. 延迟与预算汇总

| 环节 | 预算 | 固化方式 |
|---|---|---|
| 前端上报同步路径（手势完成→fetch 发起） | < 100ms | `canvas-copilot.test.tsx` 计时断言 |
| 后端 ingest 单信封（规范化+锁写） | < 100ms | `test_canvas_affordance.py::test_ingest_latency_under_100ms` |
| widget 校验（16KB spec） | < 10ms | 同上套件内隐式（60s 超时兜底） |

## 6. 测试 seam（TDD 锚点）

1. `canvas_affordance.normalize_envelope / ingest_canvas_actions`（含 FakeSituationStore）
2. `topology_derive`（手绘多边形 × 图层要素布尔运算）
3. `render_affordance_context_block / affordance_facts / tokens_from_ring`
4. `validate_widget_spec`（4 kind 合法样例 + 恶意样例矩阵）
5. `ToolExecutionPipeline.execute_tool_call` 摘取（含剥离 llm_payload）
6. `sse_mount_widget` 事件形态 + chat 路由 `canvas_actions` 字段接受（schema 层）
7. 前端：sketch-capture 纯函数、copilotSlice 行为、SpatialSketchTool 渲染+dispatch、WidgetHost 双向绑定、isWidgetSpecSafe
