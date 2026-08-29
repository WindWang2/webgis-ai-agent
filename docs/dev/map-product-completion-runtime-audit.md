# Map Product Completion Runtime — 开发审计（Phase 1）

- 日期：2026-08-30
- 基线：master `2d5c817`（Runtime v3 合并后）
- 方法：5 个独立审计 agent（Harness / MapSpec 组件 / Layer+Viewport / Export / Test 对抗）+ 主 agent 交叉验证关键集成点
- 结论前置：**系统当前把"DAG 完成"直接等价于"任务完成"，turn 在 `agent_settled` 结束时对最终地图产品零检查**；Live 与 Export 的组件语义存在系统性分叉（placement 完全不被导出读取、chart/statistics 仅 live、图例数据源不同、指北针旋转符号相反等）。

---

## 1. 当前 map-product 生命周期

```text
webgis_map_intent → SessionPlan 章节（data_requirements / analysis_steps + depends_on）
      ↓ Pi 逐工具调用（bridge dispatch）
tool result ok → apply_tool_result → _mark_progress（行状态推进 + bound_ref）
      ↓ 结果携带 mapspec_fingerprint 时
evaluate_cartographic_session（desired-state 语义检查 + 运行时观察对账）
      ↓ Pi 停止输出
agent_settled → turn 结束（task_complete 只带 step_count + 100 字摘要）
```

- 行状态唯一由 `_mark_progress` 推进（`app/services/session_plan.py:393-429`），`PlanGraph` 是纯派生投影（`app/services/gis_harness/plan_graph.py`）。
- `webgis_map_product` 的 `completeness`（`planner.py:807-854`）是 **binding-based、desired-state-only**：只看 planned layer 是否绑定，不看可见性/视口/渲染。
- `finalize_display`（`app/tools/layer_manager.py:97-292`）走 GISMutationBatch 持久化展示集，但**仅靠 SYSTEM_PROMPT 约束调用**（`app/services/chat/prompt.py:97-104`），无服务端强制。
- cartographic verdict 是三值注入（pass/fail/not_evaluated），live 观察证据"随下一次 chat 请求到达"——**turn 结束时通常只有 partial/not_evaluated**。

## 2. 完成态判断位置

| 问题 | 答案 |
|---|---|
| 系统在哪里认为 GIS task 已完成？ | **没有任何地方**。turn 结束 = `agent_settled`（`app/agent_pi_bridge.py:1691`）；`open_capabilities` 为空只是投影里的一行 `open=none`，没有消费者据此触发任何终态逻辑 |
| "all capabilities complete" 有计算吗 | 只有 `open_capabilities`（`session_plan.py:144-154`）被投影行消费；`recommended_next` 为空串时无行为 |
| DAG complete 与 map complete 之间的确定性检查 | **不存在**——这是本轮要建的核心缺口 |

## 3. Live renderer 架构

- 声明式：`MapSpecRuntime.reconcileAsync(composeLiveMapSpec(committed, hud, pending, removed))`（`frontend/lib/mapspec-runtime/runtime.ts` / `live-spec.ts`）——图层顺序只来自 committed spec。
- 命令式：`runtime-layer-registry.ts`（canonical，FIFO 256，sourceIndex O(1) 吸附）；两个 facade 委托同一存储；basemap setStyle 后先 sources 后 layers 幂等重放（`map-panel.tsx:482-488`）。
- z 序：basemap → spec layers（`syncLayerZOrder`）→ `custom-*` band（`raiseCustomOverlayLayers`）→ annotation。
- Chrome：React DOM 覆盖层（`MapSpecChrome` + `ComponentLayoutRuntime.resolveSlotLayout` 槽位求解 + `FloatingChrome` 手势，CAS 提交 placement）。

## 4. Export renderer 架构

- 客户端 canvas 快照：`map.getCanvas()`（`preserveDrawingBuffer:true`）→ `prepareExportCanvas` 裁切 → `composeLayout` 用 canvas2D **独立重绘**全部 chrome → PNG / SVG（位图包装）/ PDF（jsPDF 内嵌 PNG）。
- **exporter.ts 中 `placement`/`anchor`/`position` 出现次数为 0**：所有组件按硬编码像素坐标摆放（title 左上、scale bar 左下、legend 右下）。
- 图例数据来自 **HUD store**（`discoverLegendData`），而 live spec chrome 图例来自 **committed spec layers**——两个事实源。
- 服务端 `/export/pdf`（reportlab）与 `/export/geojson` 无前端调用方（孤儿路径）。

## 5. MapSpec component truth（逐组件）

| 组件 | MapSpec 真相 | Live | Export | 判定 |
|---|---|---|---|---|
| title | ✅ | ✅ top-center | ✅ 但 top-left、空标题回退 "WebGIS AI Agent" | DIVERGENT（placement/回退） |
| subtitle | ✅ | ✅ | canvas ✅（v3 Phase H 已修）；**PDF 文本层只读请求参数** | DIVERGENT（PDF 链路） |
| legend / categorical_legend | ✅ | ✅ 读 spec.layers[].legend_spec | 读 HUD store，不读 spec 组件 enabled | DIVERGENT（数据源+槽位） |
| continuous_colorbar | ✅ | ✅ CSS 渐变 ramp + min/max/unit | 离散色块，丢 unit | DIVERGENT（形态） |
| scale_bar | ✅ | ✅ bottom-right、3 variant | bottom-left、固定样式、不同算法 | DIVERGENT |
| north_arrow | ✅ | ✅ `rotate(-bearing)` | `ctx.rotate(+bearing)` **符号相反** | DIVERGENT（方向错） |
| attribution | ✅ | ✅ options.text | 不读；由请求 author/dataSource 代替 | DIVERGENT |
| statistics_panel | ✅ | ✅ FloatingChrome | **无** | LIVE-ONLY |
| chart_panel | ✅ | ✅ recharts（inline chart / chartRef artifact） | **无** | LIVE-ONLY |
| annotation | ✅ | ✅ | 无 | LIVE-ONLY |
| graticule | 类型存在 | 无渲染器 | 请求参数开关（UI local state） | 死配置 |
| map_border | 类型存在 | 无 | PDF 无条件画边框 | 死配置 |
| inset_map | planned | 无 | 无 | PLANNED（resolver 拒绝） |
| export_layout | ✅ | — | ✅ paper/orientation/dpi | EXPORT-ONLY（合理） |

**没有 live/export 共享的组件 resolver**——两侧各自解析 title/subtitle/placement/visible，仅共享持久化的 MapSpec 文档与生成的 catalog JSON（后者只喂 parity 测试）。

## 6. Runtime layer truth

- canonical registry 单账本成立；spec 层不进账本（契约边界）；A4（native heatmap 层定义缺账）已修。
- 可见性四方协同：committed `layout.visibility` + `pendingPresentation` + HUD 行 + turn-focus/finalize 隐藏集；`applyLayerVisibilityTransaction` 单事务 + 读回验证 + 单次 bounded repair。
- **spec-visible 但实际不渲染的三条真实路径**：ref-source 失败永久缓存（`ref-source-resolver.ts:149-154`）、`feature_count > FETCH_FEATURE_CAP` 故意不挂载、mid-patch 窗口。
- **反向**：`custom-*` 层被 `setLayoutProperty('visibility','none')` 隐藏后，style reload 重放时**复活为可见**（layerDef 不记录 layout）——P1。
- source 生命周期：登记项由 ref 生命周期治理（ADR-0079 D4 明确不加 GC）；多 custom 层共享 source 时兄弟层无 sourceDef，主层反注册后兄弟层不可重放（当前无命令铸造该形态，防御性）。

## 7. Viewport truth

- 相机是前端派生态（MapLibre uncontrolled）；持久化 opportunistic（explicit view commit / throttled POST），restore 只消费 `view.framed===true` 的 spec。
- 结果落图时的聚焦链：bridge `maybeFlyToBbox`（要求 bbox valid，退化 bbox 直接放弃）→ store-mount `focusLayer`（fetch 完成后）→ 用户/面板 focus。
- **P1 缺口**：`feature_count > 5000` 的 MVT 层跳过全量 fetch → `focusLayer` 不跑 → 若 bbox 缺失/退化则相机永不聚焦（层在、视口看不见、无人修复）；session restore 不对 restored layers 取景。
- export 使用与 live 相同相机（快照），故继承同样缺口。

## 8. 发现的问题（合并去重后）

### P0（本轮必须解决）

| # | 问题 | 证据 |
|---|---|---|
| G1 | turn 结束（`agent_settled`）对最终地图产品零检查：不验证能力终态/completeness/finalize 提交/verdict；"任务完成"="LLM 停止说话" | `agent_pi_bridge.py:1691-1692`、`pi_event_mapper.py:243-255` |
| G2 | 无任何"DAG 全终态"消费者：`open_capabilities` 无人据此触发终态逻辑 | `session_plan.py:144-154` |
| G3 | exporter 完全不读 placement（anchor + floating x/y/w/h/zIndex），用户/agent 布局在导出中静默丢失；默认槽位也互相矛盾（title/scale_bar/legend 三个组件 live 与 export 槽位不一致） | exporter.ts 0 处 placement 读取；helpers.ts:21-34 vs exporter.ts:190,217,419 |
| G4 | chart_panel / statistics_panel 仅 live；导出产品静默丢失 | component_renderers.py:80-85 |
| G5 | 视口与结果范围无任何完成时校验：层在视野外→空白地图→无人修复；`_bbox_overlap_ratio==0` 静默 | semantic_checks.py:945-1044；use-sse-stream.ts:752-781 |

### P1（本轮纳入）

| # | 问题 | 证据 |
|---|---|---|
| G6 | capability complete = "tool 返回 ok"，空 FeatureCollection 也 complete；空结果只有 review 时才发现（夜间 lane） | `tool_dispatch_service.py:592-596`、`session_plan.py:583-588` |
| G7 | bound_ref 无存在性校验（4h TTL 过期后 DAG 仍显示 complete） | `session_data_redis.py:20-24`、`plan_graph.py:338` |
| G8 | legend/colorbar 导出不读 spec 组件 enabled；数据源为 HUD store | exporter.ts:1008-1011,796-830 |
| G9 | 指北针导出旋转符号与 live 相反 | exporter.ts:246-247 vs north-arrow.tsx:38 |
| G10 | 连续 colorbar 导出退化为离散色块、丢 unit；heatmap 图例丢 min/max/unit 量化 | exporter.ts:578-595,806-812 |
| G11 | attribution 导出不读 spec 组件 | exporter.ts:299-323 |
| G12 | PDF 文本层 subtitle 只读请求参数（canvas 链已修，PDF 链未修） | exporter.ts:1084 vs 1050 |
| G13 | `custom-*` 层隐藏后 style reload 复活为可见 | renderer.ts:307-314,638-643；runtime-layer-registry.ts:267-285 |
| G14 | MVT 大层跳过 fetch 后无聚焦兜底；restore 不取景 | use-sse-stream.ts:752-757；map-state-restore.ts:93-104 |
| G15 | 后端支持矩阵过期：subtitle 标注 exporters=[] 但实际已读——"单一事实源"目录输出虚假声明 | component_renderers.py:47-49 |
| G16 | `completeness` 不含视口/可见性/组件核验；失败账本（authoring_failures）只是 advisory | planner.py:807-854；tools.py:914-917 |

### P2（低成本顺带 / 否则 defer）

| # | 问题 | 处置 |
|---|---|---|
| G17 | projection 无 product/finalize 面（Pi 看不到 "map not finalized"） | 本轮加一行有界投影 |
| G18 | task_complete 不带产品结果 | 本轮 additive 加 map_product 字段 |
| G19 | 共享 source 的重放所有权（防御性） | 本轮 registry 内所有权转移 |
| G20 | FIFO 256 驱逐静默丢重放、失败重放条目不重试、remount 绕过 renderer 注册、HUD 行不随 spec 重同步、reorder_layer 对 spec 层无效、custom 层不可交互 | **defer**（ADR follow-up） |
| G21 | 服务端 /export/pdf、/export/geojson 孤儿路径 | defer |
| G22 | graticule/map_border 死配置 | defer |
| G23 | agent layer_visibility_update 走 user-mutation CAS 通道（owner 语义） | defer（有记录的取舍） |

## 9. 纳入 / 排除

**纳入本轮 `/goal`**：G1–G19（除 defer 项）——即 Completion Runtime（contract + artifact/layer/viewport/component/layout validators + bounded repair + harness 集成 + 投影披露）与 Export Parity（shared resolver + placement/legend/colorbar/attribution/chart/statistics/指北针/PDF subtitle + 矩阵修正）+ 视口修复 + 隐藏复活修复 + source 所有权转移。

**明确 defer**：G20–G23 及 ADR-0080 已记录的 follow-up（完整布局引擎、inset/graticule/border 渲染器、source GC 治理、running 持久化）。

## 10. 架构决策预览（详见 ADR-0081）

- **后端** `app/services/gis_harness/map_completion.py`：`MapCompletionResult`（deterministic/serializable/bounded）+ 四类 validator + 有界 repair（`MAX_FINALIZATION_PASSES=2`）；触发点 = bridge 工具结果后 + turn settle；结果写入 `gis_chapter["map_product"]`（additive），投影追加一行 `Map product: …`。
- **前端** `frontend/lib/map-components/resolve-components.ts`：live 与 export 共享的组件解析纯函数；exporter 按同一 placement 语义换算像素坐标。
- **前端** `frontend/lib/map-product/finalizer.ts`：完成事件的视口校验 + 有界 fitBounds 修复（相机真相只在前端）。
- 不 fork Pi、不建第二 agent/SessionPlan/MapSpec/runtime truth；finalizer 是派生运行时逻辑。
