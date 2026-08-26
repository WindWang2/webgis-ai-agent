# ADR-0070: 地图产品组件运行时 v2 —— 浮动图表、组件目录、终态确认与图层事务

日期: 2026-08-26
状态: accepted

## 背景

audit4 修复轮（#978–#1010）后的四个系统性缺口：

1. `chart_panel` / `statistics_panel` 在后端四层注册（ComponentType / 描述符 /
   模板 / 组合槽）齐全、能进 MapSpec，但前端无渲染器、无数据通道——图表
   数据只活在 chat 消息载荷里，与地图零关联。
2. `FINALIZE_DISPLAY` 是唯一不碰 MapLibre 的地图命令：只更新 HUD + pending
   overlay，无读回验证、无 confirmed/store_updated 证据、无 fingerprint
   （游离于 cartographic gate 之外）。
3. Agent 可见性操作（set_layer_status/display_layer/finalize_display）从不
   持久化到后端 desired state——reload 后 Agent 决策丢失；与用户 UI 路径
   形成两套 regime。
4. `webgis_component_update` 只能改既有组件；Agent 无「发现组件」工具面；
   布局只有六槽锚点，无自由放置；variant 目录三处重复。

## 决策

### D1 组件 placement（typed 布局）

`CartographyComponent.placement?: {mode: anchor|floating, anchor?, x?, y?,
width?, height?, zIndex?, collapsed?}`。anchor 模式与 `position` 双写一致
（单一真相）；floating 模式保留 position 兜底。旧 MapSpec 无 placement
完全兼容。

### D2 图表数据通道（单一协议）

chart_panel 的数据协议 = chat ChartData（bar/line/pie/scatter 单序列，
`adaptChartData`/`validate_chart_payload` 同契约同上限：100KB/500 点/
200 字符）。小载荷 inline（`options.chart`），大载荷（>32KB）存
`ref:chart-*` artifact（`options.chartRef`，`GET /chat/sessions/{sid}/
chart-artifacts/{ref}` 拉取）。禁止第二套图表 schema。

### D3 组件突变单一入口

- 后端：`PatchComponentIntent`（生命周期引擎事务，CAS-ready）+ 用户路由
  `patch_component` intent + `mapspec_store.patch_component` 门面。
- Agent：`webgis_component_update` 增强（create/upsert、placement、
  variant、chart/stats payload、expected_revision 乐观并发——用户拖拽优先
  于旧 Agent 决策）。
- 发现：`webgis_component_catalog` 单工具返回当前组件状态 + variant 目录
  + mutation_revision。
- `generate_chart(attach_to_map=true)` 复用同一入口把图表挂到地图。

### D4 浮动交互（MapSpec 单一状态源）

FloatingChrome：pointer 拖拽（rAF 瞬态、本地 state 仅手势期间）、resize、
collapse、hide、reset。手势结束一次 `patch_component` 提交；乐观 override
store 与 committed spec 自动对账。禁止把手势状态永久留在 React local
state、禁止 per-frame 提交。

### D5 图层可见性事务（深接口）

`applyLayerVisibilityTransaction`：身份解析（layer-identity.ts 单点）→
desired（store+pending）→ runtime（setLayoutProperty 即时生效）→
durability（后端 MapSpec CAS 顺序提交）→ postcondition（读回验证）→
honest confirmed/store_updated。layer_visibility_update 与 finalize_display
共用；UI toggle 走既有 commitLayerPresentation（同一 CAS 语义）。

### D6 finalize 终态确认

后端：结果携带 `mapspec_fingerprint`（进入 cartographic gate）+ 展示集
服务端 desired 持久化 + `final_display` 证据块。前端：真实事务 + 证据
（confirmed/visible/hidden/unresolved layer ids）+ 有界修复（单次重验，
绝不循环）。boundary + 用户 pin（`_userPinned`）豁免。

### D7 variant 单一权威

描述符目录（component_registry.py `.variants`）是唯一 variant 事实源；
`coerce_variant` 校验/回退；前端契约文件
`component-catalog.generated.json`（`python -m
app.lib.cartography.export_component_catalog` 再生）+ 双侧 parity 测试。

## 后果

- 组件↔渲染器 parity 由契约测试强制（rendererRequired=true 的类型必须有
  注册渲染器）；新增组件类型必须同时落两侧。
- Agent remove/visibility 现在同步 desired state——zombie/复活图层路径
  关闭；store-only 目标诚实 store_updated（非收敛）。
- map-panel.tsx 1225→926 行（#1000 系列抽取 use-cartographic-observation /
  use-hover-tooltip / use-feature-selection）。
- 已知取舍：recharts 仍在 map chrome chunk 内（懒加载会破坏同步测试断言，
  记为后续优化）；服务端 finalize 只持久化展示集（隐藏集裁决权在前端——
  group/pin/boundary 语境只有前端有）。
