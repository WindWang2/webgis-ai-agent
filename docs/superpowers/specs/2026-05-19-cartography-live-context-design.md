# 制图实时上下文与可视反馈 — 设计文档

**日期**: 2026-05-19
**状态**: 已批准，待实现
**主题**: 让地图视图自解释，让聊天里的制图结果可视化

## 背景与动机

当前制图栈三块缺失，造成「专题图生成后用户在地图上看不出所以然」：

1. **地图装饰要素只在导出时合成** — 指北针、比例尺、标题在 `export_thematic_map`
   生成 PNG/PDF 时由前端合成；地图浏览态下完全不显示，用户无法直观判断方向、
   尺度与图件主题。
2. **图例只覆盖 `choropleth` 一种** — `ThematicLegend` 浮窗仅在 `metadata.thematic_type
   === "choropleth"` 时显示。LISA（已有 5 类色谱）、`kde_contours`、`h3_binning`、
   `heatmap_data` 等其他专题输出全部没有图例可看。
3. **聊天里没有制图结果的可视反馈** — 工具返回 JSON 文本后，用户必须切到地图
   才能知道做了什么；多步分析对话中很容易丢失上下文。

附带问题：`apply_layer_style` 的工具描述里引用了 `apply_thematic_style` 和
`update_layer_appearance` 两个不存在的工具名——LLM 编排时会被误导。这是工具
描述清理一并修复。

## 设计原则

**纯加法、零核心重构。** 不动 MapLibre 渲染管线，不引入新模式（探索 vs 出图），
不新增 SSE 通道。所有改动落在三处增量：

- 后端工具输出 **多一个标准化的 `legend_spec` 字段 + 可选的 `layer_meta.title`**。
- 前端把 `ThematicLegend` 拆成 **legend router + 四种子组件**，按 `legend_spec.type`
  分派。
- 地图浮层与聊天结果卡是**独立可见性**的新组件，与工具结果通过 store 解耦。

**「至少有一个带 `legend_spec` 的可见图层」是图例 + 装饰 + 标题三者的统一显示
条件。** 探索阶段不打扰，专题图一出立即给上下文。

**任何环节失败都降级。** `legend_spec` 缺失/畸形 → 不渲染图例，地图装饰也跟着
退场；不抛错、不阻塞工具结果，主流程不感知。

## 架构总览

### 数据契约 — `legend_spec` 联合类型

工具返回的 payload 顶层多一个可选字段 `legend_spec`，四种 `type` 之一：

```ts
type LegendSpec =
  | { type: "graduated";
      field: string;
      breaks: number[];           // 长度 = 类别数 + 1
      palette: string;            // 调色板键名，例如 "YlOrRd"
      palette_colors: string[];   // 实际渲染色（每类一色，hex）
      unit?: string;              // 可选单位
      format?: "number" | "percent" | "currency"; }
  | { type: "continuous";
      field?: string;
      min: number;
      max: number;
      palette: string;
      palette_colors: string[]; } // 色带采样（≥3 色）
  | { type: "categorical";
      field: string;
      categories: { key: string; color: string; label: string }[]; }
  | { type: "divergent";          // stub，本期不实现 UI 之外的逻辑
      field?: string;
      center: number;
      min: number;
      max: number;
      palette: string;
      palette_colors: string[]; };
```

附带 `layer_meta.title?: string`——若给出，前端会写入 store 驱动地图标题片。
没给则标题片用图层名兜底。

### 后端：在 `cartography_service.py` 中实现 `build_legend_spec`

`CartographyService.build_legend_spec(style_def)` 是单一权威转换点：把现有的
`build_thematic_style` 输出（choropleth 的 `{breaks, colors, palette}`；lisa 的
`{categories, colors, legend_labels}`）映射成对外契约。其他工具（h3_binning、
kde_contours、heatmap_data 的 raster/grid 模式）在自己的输出处直接构造对应 type
的 dict（轻量，无需走 service）。

### 前端：图例 router

`thematic-legend.tsx` 当前文件保留组件名 `ThematicLegend` 作为新的 router 入口
（避免改 `map-panel.tsx` 的 import），内部按 `legend_spec.type` 分派：

```
ThematicLegend (router)
├─ GraduatedLegend     ← 现有 choropleth UI 抽出，沿用「按类隐藏」交互
├─ ContinuousLegend    ← 色带 + min/max 标签
├─ CategoricalLegend   ← 类块 + 标签（LISA 的 5 类直接落这里）
└─ DivergentLegend     ← 占位 stub，渲染 ContinuousLegend 直到本期外补完
```

多个带 `legend_spec` 的图层同时可见时，`map-panel.tsx` 中原本的浮窗 wrapper
（条件渲染 `<ThematicLegend>` 的容器）改为对每个可见 thematic 图层渲染一份
`<ThematicLegend>`，按图层名分组纵向堆叠。

### 前端：地图装饰

新文件 `map-decorations.tsx` 导出三个子组件：

- `NorthArrow` — 右上角浮窗，订阅 `map.bearing`；2D web 地图通常 bearing=0，
  组件仍保留旋转能力以兼容未来 3D 视角。
- `ScaleBar` — 右下角浮窗（地图归属信息上方），订阅 `map.zoom` 与中心纬度计算
  米/像素，输出最近的人类友好刻度（50m / 100m / 200m / 500m / 1km …）。
- `MapTitle` — 顶部居中浮窗的标题片，从 store 的 `cartographyTitle` 读取。

**可见性 = `useHudStore.layers.some(l => l.visible && l.metadata.legend_spec)`**。
这一条件由 `map-panel.tsx` 算一次，传给图例容器和 `MapDecorations` 共用，避免
两边判断漂移。

### 前端：聊天制图结果卡

新文件 `cartography-result-card.tsx`：

```
┌─────────────────────────────────────┐
│ 🎨  专题图 · 成都人口分布            │  ← title from layer_meta.title
│ ▮▮▮▮▮  (palette swatches)           │  ← 5 个色块 / 色带
│ 字段: pop  ·  5 分位                 │
│              ╭──────────────────╮   │
│              │ 高亮此图层  →    │   │  ← clickable
│              ╰──────────────────╯   │
└─────────────────────────────────────┘
```

`tool-call-card.tsx` 按工具名（`create_thematic_map`、`h3_binning`、`kde_contours`、
`heatmap_data`）分派到此卡片 variant；其他工具继续原逻辑。「高亮此图层」按钮触发
新的 store action `focusLayer(layerId)`：`map.fitBounds(layer.bbox)` + 在浮窗图例
对应区域加 200ms 边框闪烁动画。

## 数据流

```
1. 用户："做成都市人口分布的专题图"
2. AI 调 create_thematic_map(geojson, field="pop", method="quantiles", k=5)
3. 后端 CartographyService.build_thematic_style → build_legend_spec → 返回:
   {
     geojson, style,
     legend_spec: { type:"graduated", field:"pop", breaks:[...], palette_colors:[...] },
     layer_meta: { title:"成都人口分布" }
   }
4. SSE tool_result → map-action-handler:
   - addLayer(geojson, style)
   - store.layers[i].metadata.legend_spec = legend_spec
   - store.cartographyTitle = layer_meta.title
5. 前端两路同步渲染:
   - map-panel.tsx: 浮窗 wrapper 渲染 ThematicLegend (router) → GraduatedLegend；
                    MapDecorations 渲染 NorthArrow + ScaleBar + MapTitle
   - tool-call-card.tsx: 分派到 CartographyResultCard
```

## 文件结构

| 文件 | 动作 | 职责 |
|------|------|------|
| `app/services/cartography_service.py` | 修改 | 新增 `build_legend_spec(style_def)` |
| `app/tools/cartography.py` | 修改 | `create_thematic_map` 返回包含 `legend_spec` + `layer_meta.title`；修正 `apply_layer_style` 描述里不存在的工具引用 |
| `app/tools/spatial.py` | 修改 | `heatmap_data` raster/grid 模式输出 `continuous` `legend_spec` |
| `app/tools/advanced_spatial.py` | 修改 | `h3_binning` 输出 `graduated` `legend_spec` |
| `app/tools/spatial_stats.py` | 修改 | `kde_contours` 输出 `continuous` `legend_spec` |
| `frontend/lib/map-kit/types.ts` | 修改 | 新增 `LegendSpec` 联合类型 |
| `frontend/components/map/thematic-legend.tsx` | 修改 | 改造为 router；保留组件名 |
| `frontend/components/map/legends/graduated-legend.tsx` | 新建 | 抽出原 choropleth UI |
| `frontend/components/map/legends/continuous-legend.tsx` | 新建 | 色带 + min/max |
| `frontend/components/map/legends/categorical-legend.tsx` | 新建 | LISA 等分类 |
| `frontend/components/map/legends/divergent-legend.tsx` | 新建 | stub |
| `frontend/components/map/map-decorations.tsx` | 新建 | NorthArrow + ScaleBar + MapTitle |
| `frontend/components/map/map-panel.tsx` | 修改 | 挂载 `MapDecorations`；图例与装饰共用可见性判断 |
| `frontend/components/map/map-action-handler.tsx` | 修改 | 从 tool 结果抽 `legend_spec` 与 `layer_meta.title` 写入 store |
| `frontend/components/chat/tool-call-card.tsx` | 修改 | 按工具名分派到 `CartographyResultCard` |
| `frontend/components/chat/cartography-result-card.tsx` | 新建 | 制图结果卡 |
| `frontend/lib/stores/useHudStore.ts`（或对应 slice） | 修改 | 新增 `cartographyTitle` 状态与 `focusLayer` action |

## 错误处理

| 场景 | 行为 |
|------|------|
| 工具返回里没有 `legend_spec` | router 渲染 `null`；地图装饰 + 标题随之收起 |
| `legend_spec` 字段缺失/类型错 | router 同上；前端 `console.warn` 一次，不抛错 |
| LISA 无显著聚集（全部 NS） | `categorical` 仍渲染 5 类，无数据的类按 `opacity: 0.4` 置灰 |
| 多个 thematic 图层可见 | 图例按图层名分组堆叠；装饰只渲一份；标题取最近一次设置 |
| 用户隐藏所有 thematic 图层 | 图例 + 装饰 + 标题同步收起 |
| `focusLayer` 找不到 bbox | fitBounds 跳过，仅触发图例边框闪烁 |

## 测试策略

**后端**（pytest）：
- `tests/test_cartography_service.py` — `build_legend_spec` 对 graduated / categorical
  两种 style_def 输入均产出符合契约的 dict
- `tests/unit/test_spatial_tools.py`（已有）扩展 — `heatmap_data` raster/grid 模式
  返回 `legend_spec`，native 模式不返回
- `tests/unit/test_advanced_spatial_tools.py`（若不存在则建）— `h3_binning` 返回
  `graduated` legend_spec
- `tests/unit/test_spatial_stats.py`（若不存在则建）— `kde_contours` 返回
  `continuous` legend_spec

**前端**（vitest + RTL）：
- `thematic-legend.test.tsx` 既有用例迁入 `graduated-legend.test.tsx`；新增
  `thematic-legend.test.tsx` 验证 router 按 type 分派、未知 type 返回 null
- 新增 `continuous-legend.test.tsx` / `categorical-legend.test.tsx`：色带 / 类块渲染
- 新增 `map-decorations.test.tsx`：可见性条件、刻度计算（mock map.getZoom）
- 新增 `cartography-result-card.test.tsx`：色板渲染 + 「高亮此图层」点击触发
  `focusLayer` 调用

**契约**：前后端共用一份 `LegendSpec` 形状描述（前端 ts，后端注释），任一端
改动需同步另一端的测试。

## 验收标准

- 触发 `create_thematic_map` / `h3_binning` / `kde_contours` / `heatmap_data` 后：
  - 地图浮窗图例自动出现，类型与工具匹配
  - 地图右上指北针、右下比例尺、顶部标题片同步出现
  - 聊天里对应工具的结果显示为 `CartographyResultCard`
- 隐藏全部 thematic 图层后，图例 + 装饰 + 标题片消失
- LISA 专题图渲染出 5 类色块图例，含 HH/LL/HL/LH/NS 标签
- 工具不返回 `legend_spec`（旧行为）时，地图不报错、不显示新组件
- 既有 `thematic-legend.test.tsx` 等已有测试在改造后全部通过

## 范围外（明确不做）

- **不**引入「出图模式」开关（路线 B 的方向，留作未来）
- **不**新增 `cartography_state` SSE 通道（路线 C 的方向）
- **不**改 `export_thematic_map` 的导出排版逻辑（导出质量是单独议题）
- **不**新增分级符号 / 点密度 / 双变量等专题类型（仅打通图例契约，新类型留待
  独立 spec）
- **不**实现 `DivergentLegend` 的完整 UI（先 stub，等 hotspot z-score 出图时再补）

## 与既有工作的关系

本 spec 在 `plan-first-agentic-loop` 分支之后落地，依赖该分支的工具描述加固
（Task 10）— `apply_layer_style` 工具描述里不存在的 `apply_thematic_style` /
`update_layer_appearance` 引用，本 spec 顺手修正。

LLM 端无需感知 `legend_spec`：工具描述告诉它「调用这些工具即可获得专业制图
输出」即可；`legend_spec` 是后端→前端的契约。
