# GIS Harness —— GIS 领域智能层

> 状态：已实现（2026-08）。代码：`app/services/gis_harness/`。
> Spec：`specs/gis-harness-cartography-recipes-spec.md`。

## 1. 定位

Pi（Agent Runtime）负责语义理解与工具调用发起；**GIS Harness 负责 GIS 领域
智能**——意图解析、制图方法（Recipe）选择、资格判定、回退决策、产品规划、
组件组合与证据。Harness 不执行工具、不持有 runtime state：执行仍归
`ToolDispatchService`，制图期望状态仍归 MapSpec（唯一 desired cartographic
state）。

```
Natural Language
      ↓  webgis_map_intent（确定性 resolver + LLM hint 显式合并）
MapRequestIntent
      ↓  Recipe selection（确定性排序；agent 可建议，代码裁决）
CartographyRecipe（候选）
      ↓  数据工具执行（Pi/ChatEngine → ToolDispatchService，能力面不变）
Spatial Profile（ref descriptor 派生，零全量扫描）
      ↓  Recipe Eligibility 复检（几何/最小点数/字段 —— 代码侧确定性）
MapProductPlan（终稿，含 fallback 决策记录）
      ↓  webgis_map_product（角色绑定 + 缺层补齐 + 组件落 MapSpec）
MapSpec（layers + layout.components）
      ↓  既有编译/渲染链
MapLibre live 渲染 ─ export（同一份组件描述）
      ↓
PiAgentHarness Evidence（Intent/Recipe/Fallback/Component/Completeness）
```

## 2. 职责边界（不变量）

| 概念 | 职责 | 代码 |
|---|---|---|
| Pi | Agent Runtime（语义、发起调用） | `vendor/pi` + `app/agent_pi_bridge.py` |
| GIS Harness | 领域智能：intent/recipe/eligibility/fallback/plan/components/evidence | `app/services/gis_harness/` |
| ToolRegistry | GIS 能力面 | `app/tools/registry.py` |
| ToolDispatchService | 执行 + ref + evidence（未改架构） | `app/services/tool_dispatch_service.py` |
| MapProductPlan | GIS/制图执行计划（期望产品，非工具脚本） | `gis_harness/planner.py` |
| MapSpec | 唯一制图期望状态（含 `layout.components`） | `app/services/mapspec/` |
| MapLibre | Runtime renderer | `frontend/` |
| PiAgentHarness | Evidence + Evaluation | `app/lib/harness/` |

## 3. 热力半径契约（P0 修复）

历史问题：`radius` 一词同时承担米（工具 schema，raster 模式真实消费）与像素
（native 渲染链路消费）两种语义，各层用互斥的隐式猜测（`>100 回落 30`、
`4–60 直通`、`15 兜底`……六个约定并存）消化同一个数字。

契约（`app/lib/cartography/heatmap_contract.py`，前端镜像
`frontend/lib/map-kit/renderer.ts::resolveHeatmapRadiusPx`）：

- **`radius_px`** —— 视觉热力核半径，MapLibre 屏幕像素，默认 30，clamp [4,80]；
- **`bandwidth_m`** —— 分析密度带宽，米（raster/grid 的 `sigma = bandwidth /
  cell_size` 真实输入），默认 1000；
- **legacy `radius`** —— 唯一归一化边界消化：`bandwidth_m = radius`（尊重
  schema 米语义）；视觉半径在 4–60 历史直通窗口内延续 px，超窗回落默认 30px
  并携带 `legacy_radius_visual_default_applied` 迁移警示。**米值绝不再被当作
  像素消费**（测试证明 1000 ≠ 1000px：`tests/unit/test_heatmap_contract.py`）。

核心链路（converter / palettes / renderer / adapter / export）只消费显式
字段。MapSpec 热力层新增 `heatmap` 兄弟键（与 `legend_spec` 同先例，不进
MapLibre paint）记录 `{radius_px, bandwidth_m, radius_source}` 供审计与 HUD
投影（`_runtime_patch` → `style.radius_px`）。

## 4. MapRequestIntent

`gis_harness/intent.py`。typed（Pydantic）/ serializable / deterministic 的
意图契约：`scope / subject / entity_type / geometry_expectation / task /
measure / group_by / analysis_intents / cartography_intents / output_intents /
export_intents / confidence / assumptions / matched_rules`。

确定性 resolver（规则序 = 特异性，先命中先停；无命中兜底
distribution_overview 并显式记录 assumption）。LLM hint 经
`merge_intent_hints` 白名单合并，全部记录进 `hint_applied`；护栏：**定量密度
（analytical_density）不可被 hint 降级为视觉任务**。

关键判定（Golden Cases）：

| 请求 | task | 主表达 |
|---|---|---|
| 成都小学的分布情况 | distribution_overview | 热力 + 点 + 行政聚合候选 |
| 成都各区小学数量 | administrative_statistic | 行政聚合 + choropleth |
| 成都每平方公里小学密度 | analytical_density | 定量密度（非视觉热力） |
| 成都小学哪里最集中 | concentration_analysis | KDE/热点族 |
| 给我看看成都小学 | simple_view | 轻量点图（不过度分析） |

## 5. CartographyRecipe

`gis_harness/recipes.py`。Recipe 描述「怎么制图」：intent 匹配面、几何要求、
eligibility 规则（`visual_heatmap: min_points=10 ∧ geometry=point` 等）、
preferred/optional analysis（**能力 id，非工具名**）、primary/secondary
cartography、默认组件、声明式 fallbacks、export profile。

注册表仿 TemplateRegistry 的 indexed 思路（O(1) by id + by task）。
首批 8 个：`poi_distribution_overview`、`point_density`、
`administrative_choropleth`、`categorical_distribution`、`hotspot_analysis`、
`proximity_analysis`、`accessibility_analysis`、`raster_distribution`。

**eligibility 是代码侧确定性检查**（`check_eligibility`），输入是 Spatial
Profile（descriptor 派生）；agent 只能建议 recipe，不能取代检查。数据回来后
必须复检（`MapProductPlanner.finalize_with_profile`）——
point_count=7 自动禁用热力层、升级点图为 primary，并记录结构化 fallback
证据 `{from, to, reason_code, evidence}`（如
`INSUFFICIENT_POINTS: {point_count: 7, min_points: 10}`）。

## 6. MapProductTemplate / MapProductPlan

- **MapProductTemplate**（`product_templates.py`）：完整产品组合的描述——
  recipe + 图层角色（primary/secondary/reference）+ 组件 + 输出物。描述
  **期望成果**而非工具调用序列；工具解析由 `CAPABILITY_TOOLS` 能力表在执行
  期完成（工具替换时模板无需重写）。
- **MapProductPlan**（`planner.py`）：`plan_from_intent`（draft，数据未到手）
  → `finalize_with_profile`（eligibility 复检 + 图层裁决 + 终稿组件 + 完整
  性评估）。plan_id 由 (query, recipe) 决定性派生 —— 可回放、可 diff。

## 7. CartographyComponent 与组件化制图

`gis_harness/components.py`。统一 schema：`id / type / enabled / position /
priority / style / options / compatibility`。首批类型：basemap、legend、
continuous_colorbar、categorical_legend、north_arrow、scale_bar、title、
subtitle、annotation、graticule、map_border、attribution、statistics_panel、
chart_panel、export_layout。

- **legend ≠ colorbar**：choropleth（分级）→ 离散 legend；heatmap/连续栅格 →
  continuous_colorbar；分类专题 → categorical_legend。由 Recipe/主表达决定。
- **组件可替换**（§11）：「换一个指南针」「色条竖向」「比例尺左下」「不要
  指南针」= `mutate_component` 局部突变，只动命中组件，绝不触发数据重查/
  重分析（`webgis_component_update` 工具 + 突变证据
  `component_mutation_evidence`，断言图层数不变）。
- **组件进入 MapSpec**：`layout.components`（与 legend/controls 并列；
  lifecycle `SetLayoutIntent` 事务化写入 + priority/id 稳定排序；非法条目
  原子拒绝）。live 渲染（`frontend/components/map/map-spec-chrome.tsx`）与
  export（`exporter.ts` 读取组件作为默认 title/compass/scalebar）共用同一份
  —— **Live 与 Export 不再是两套版面参数**。

## 8. Agent 工具面（3 个新工具，tier-2）

- `webgis_map_intent(query, hints…)` —— 意图 + 候选 recipe + 计划骨架 +
  能力→工具解析。确定性、无副作用。
- `webgis_map_product(query, session_id, layer_ids / primary_ref / overlay_refs,
  title…)` —— 复检资格、绑定已有图层角色（按类型确定性映射
  heatmap→primary / circle→point_overlay / fill→choropleth）、补齐缺失图层
  （经既有 converter 授权）、写 `layout.components`、返回完整产品证据。
- `webgis_component_update(component_id|type, enabled/position/style/options)` ——
  组件局部突变。

`webgis_map_product` 的 ref 参数在 registry 解引用 skip-list 中
（`primary_ref` / `overlay_refs`），ref 游标不被透明内联为大 payload。

## 9. Harness Evidence

扩展（不建第二套 evaluator）：`ToolCallEvidence.map_product`（新
`MapProductEvidence`，有界转录 `map_product_evidence`：intent_resolution /
recipe_selection / recipe_eligibility / fallback_decisions /
component_selection / completeness）。Pi bridge 与 legacy seam 均转发该键。
新维度 `MapProductCompleteness`（阈值 80）：**仅有产品证据时评估；无证据
run 豁免（not_applicable_exempt），绝不为 PASS**（`require_map_product=True`
可收紧为策略失败）。

## 10. 地图模型库（Map Model Library，2026-08 增补）

`app/lib/cartography/model_library.py`（机器可读的权威目录，不是第二套执行引擎）：

| 制图类别 | 模型 id | MapLibre 图层 | 框架对照 | 色系 | 能力解析 |
|---|---|---|---|---|---|
| 热力概览 | `visual_heatmap` | `heatmap`（radius 屏幕像素） | deck.gl Heatmap / kepler heatmap / QGIS heatmap | 感知均匀 `classic/magma/viridis` | `heatmap_data` |
| 行政分级 | `administrative_choropleth` | `fill` + 渐变填充 | kepler geojson / QGIS graduated / GeoDa choropleth | sequential `YlOrRd/Blues/Greens` | `spatial_aggregate` + `admin_boundary` |
| 格网聚合 | `aggregate_grid` **(新增)** | `fill` | deck.gl HexagonLayer/GridLayer / kepler hexbin | sequential `YlOrRd` | `grid_binning→h3_binning/fishnet_grid` |
| 比例符号 | `proportional_symbol` **(新增)** | `circle`（面积∝√value） | deck.gl Scatterplot / kepler point size | sequential `Blues` | `poi_query` |
| 分类专题 | `categorical_thematic` | `fill` | QGIS categorized / GeoDa Unique Value | qualitative `Set1/Set2/Dark2/Pastel1` | `spatial_stats` 类别分解 |
| 热点显著性 | `hotspot_overlay` | `fill` + rule-based | GeoDa LISA | diverging `RdBu` | `hotspot_analysis` |
| 栅格面 | `raster_surface` | `raster` | QGIS paletted | `Viridis/Inferno/Plasma` | `local_raster` |

数值分级：`quantiles` / `equal_interval` / `natural_breaks`（Jenks DP）+
`std_dev` **(新增，QGIS 均值±0.5 SD)** + `head_tail` **(新增，Jiang 2013 重尾)**，
统一由 `CartographyService.classify` 计算；调色板扩充 9 个（`Oranges/Purples/RdYlGn/RdBu/Set1/Set2/Dark2/Pastel1/Inferno/Plasma`，ColorBrewer 官方 hex），`validate_model_library()` 断言跨引用完整性。

目录完整但运行时待接线（诚实标记 `planned`，`MapModelRegistry.planned_ids()`）：
`flow_od_arc`（deck.gl ArcLayer）、`extrusion_3d`（MapLibre fill-extrusion / GeoDa 2.5D）、`isoline_contour`（deck.gl ContourLayer）。

## 11. 性能

- Recipe/ProductTemplate 注册表：进程级 indexed dict，O(1) lookup；
- eligibility 消费 descriptor 派生 profile（零 FeatureCollection 全量扫描）；
- 组件/计划均为有界结构化 dict（fallback ≤ 记录数上限，evidence 转录有界）；
- 大 GeoJSON 仍走 ref + fetch-on-demand：产品组装工具按 ref 取数，不把
  FC 内联进 LLM payload / SSE / evidence。

## 11. 兼容性

- legacy `heatmap_data.radius` 仍被接受（归一化 + 警示），显式
  `radius_px`/`bandwidth_m` 优先；
- MapSpec `layout.components` 为可选键：旧 spec / 旧 session 无组件时行为
  完全不变（HUD chrome 照旧）；有组件时 MapSpecChrome 专职渲染并让位旧
  MapDecorations，避免双份；
- template ids / 工具名 / 既有 MapSpec session 全部兼容；导出请求参数 >
  spec 组件 > 内置默认的优先级保证旧导出调用不变。

## Pi 路径与规划链（#726 审计裁决）

`USE_NEW_AGENT=1` 时（`app/api/routes/chat.py` 的 `pi_event_generator`），回合
**不经过** legacy 规划链（classify_followup → should_plan → make_plan →
CanonicalPlan → ToolCatalog schema 子集）。Pi Agent Runtime 以单一
`webgis_execute` 代理工具 + 各工具的 promptSnippet 自主选择并串行执行工具；
`webgis_map_intent` / `webgis_map_product`（statistics/report 激活域）承担
GIS 侧的产品规划职责。

由此产生的架构事实：

- **CanonicalPlan / decision_log 仅是 legacy 路径的计划真相源**；Pi 会话没有
  CanonicalPlan，也不产生 plan evidence。规划/工具选择质量指标只在 legacy
  路径上定义。
- **制图质量闭环两条路径共享**：desired MapSpec 生命周期、runtime 观察、
  ACK、verdict 注入（`cartography_context` / `webgis_cartography_status`）在
  Pi 会话上同样成立——差异只在『回合级任务规划』这一层。
- 把 legacy 规划链移植进 Pi 回车 preamble 是独立的 roadmap 项，需要 Pi 侧
  多工具 schema 支持，不在本 seam 隐式实现。

## V4 — Autonomous Spatial Reasoning & Execution Runtime（ADR-0104）

> 状态：已实现（2026-09，`feat/gis-harness-autonomous-runtime-v4`）。
> 审计基线：`.agent-work/harness-v4/01..08`（只读审计，master@16d1c70）。

V4 在既有契约（Recipe/WorkflowProfile、ToolDescriptor、ArtifactContract、
18 阶段链常量）之上，把生产运行时从「每轮从自然语言重推导 + 整体重验」
升级为**状态驱动、可恢复、可观测**的执行面：

### WorkflowInstance（运行态状态机）

- `app/services/gis_harness/workflow_instance.py`：从章节事实纯派生的
  实例块（`gis_chapter["workflow_instance"]`，additive 单键）。单调
  `state_revision`、canonical `state_fingerprint`、逐阶段证据版本
  （staleness = 指纹失配）、有界转移记录。
- 事件维度化（data/algorithm/parameter/style/output）：style-only 突变
  只推进呈现面，科学阶段零触碰；数据到位自动重算科学契约（解除方向
  回写 `workflow_contract`——同一纯评估器；恶化方向只披露）。
- `rows_fingerprint` V2：`resolved_algorithm` + params 哈希可见 —— 参数
  编辑不再被终验去重门漏过。
- 开关：`GIS_WORKFLOW_INSTANCE=0`。

### SpatialGoalGraph（方法学骨架）

- `app/services/gis_harness/goal_graph.py`：goal/acquire/inspect/validate/
  transform/analyze/aggregate/model/compare/verify/cartography/observe/
  deliver/disclose 十四类节点的确定性展开（零 LLM），typed 依赖边，
  可 diff / 可序列化 / 有界（≤48 节点）。
- 成都小学公平性类目标：分母获取 + 人均归一化以 BLOCKED 科学节点显性
  出现在图上（红线可见，不藏文案）。
- `validate_candidate_graph`：LLM 建议图由确定性 validator 收敛
  （词表/规模/环/capability 对账）。

### 数据画像 → 算法裁决（打通 deferred seam）

- `DatasetProfile.to_resolver_profile` 为唯一适配器，emit 完整事实词表
  （geometry/CRS class/feature count/字段/null/bands/temporal）；V3 画像
  经 `from_profile_v3` 接入。
- `RefDescriptor.crs`（additive）：descriptor 驱动的 finalize 路径上
  projected-CRS 硬门复活；precondition 缺事实 = deferred-PASS（不虚构
  违反）；运行期重裁决由 `workflow_engine` 消费事实
  （`GIS_RUNTIME_PROFILE_GATES=0` 关停）。
- 插值族：事实投影（点数/度量/CRS/趋势）驱动候选与 hint —— 事实胜过
  文本点名，硬门最终裁决；backend 选择在计划期以纯函数记录。

### Tool Retrieval V4

- `tool_surface_v3` 之上的确定性 rerank：phase / artifact 语义类型 /
  CRS 语义 / scale 档 / 延迟内存档 / deterministic / 近期失败降权 /
  fallback 补位 / 续跑加成 —— 全部可解释（score_components）。
- 词法索引键入完整 descriptor 指纹；`ToolSelectionContext` 三个可选
  证据面（artifact 类型 / 近期结果 / 续跑工具）在生产装配点接入。
- 语料 3,028 条（`app/evaluation/retrieval_corpus.py`）+ 离线门：
  recall@10 ≥0.98、tier-3 泄漏 = 0、schema 字节预算内。
- 开关：`GIS_TOOL_RETRIEVAL_V4=0`（证据门：无上下文证据时与 V3 逐位一致）。

### Context Memory Runtime V4

- `app/services/chat/context_policy.py`：把 `GisBudgetAdvisor` 的建议
  **执行**化（KEEP pin / CONDENSE / OFFLOAD_REF / SUMMARIZE /
  DROP_OLDEST / RELOAD_REF），装配期生效；安全事实 pin 不再被折叠丢失。
- ref 驱逐落盘 + ref_resolver 持久回退（reload 有来源）；溢出一次
  确定性 re-trim 重试；未配置窗口时如实报 unknown（不再恒报 over_budget）。
- 开关：`GIS_CONTEXT_POLICY=0`。

### Specialist Subagent Team

- 角色注册表扩至 9 个专家角色（含 spatial_scientist / algorithm_reviewer /
  map_observer / result_verifier / doc_crosschecker），每个声明工具
  allowlist / mutation policy / 轮数与工具调用与 heavy 预算 / 墙钟预算 /
  model role / expected_outputs / failure_behavior。
- `spawn_subagent` 暴露 `role`（未知角色 fail-closed）；有界并行 spawn
  （≤2 并发）+ 父预算汇总 + 部分失败如实 PARTIAL。
- 角色约束只能收窄调用方权限（既有 intersection 语义不变）。

### Map Observation / Verification 闭环强化

- `chart_required` 并入 required 槽面：期望态（组件缺失）+ 观察态
  （渲染缺失）+ 修复通道三面覆盖。
- 完成期审计：地图模型兼容性复核（`F_MAP_MODEL_MISMATCH`）、全透明
  结果层结构代理（`F_LAYER_TRANSPARENT`，诚实标注非像素验证）、
  zoom 形态 viewport 服务端近似 bbox（extent 检查不再失明）。
- 不确定性披露维要求正证据（`chapter["uncertainty_disclosures"]`）；
  `task_complete` 布尔 = 裁决 ∈ READY* 且 final_map ∈ verified*
  —— BLOCKED_* 不再以 disclosure-only 冒充完成。

### 18 阶段证据链 + 评测

- 发射点 5/18 → 18/18（planner/dispatch/finalizer/observation/settle 全
  缝接入；emit-once 去重；无 turn 上下文 = 诚实不发射）。
- 链持久化：会话 JSONL（`trace_store`，≤64 turn/会话，
  `GIS_TRACE_PERSIST=0` 关停）→ `chain_gate`（≥0.95，N/A 阶段显式
  披露，缺发射绝不伪装）。
- Runtime 语料：24 情境 × 语义族 × scope × zh/en × 句式 = 3,456 条
  可执行 plan-tier 案例（`runtime_corpus.py`，含回归套件可追溯性）+
  126 个 ≥2-turn 复合 E2E 场景；组合确定性语料 ≥23K。

### 兼容性红线（V4 全量）

- 冻结缝（ToolRegistry/CapabilityRegistry/AlgorithmRegistry/SessionPlan/
  MapSpec/ArtifactContract/ExecutionPlan）零破坏性变更；所有新运行面有
  显式开关且缺省保持旧行为的数据路径（事实缺席 = 诚实 unknown，不虚构）。
- `rows_fingerprint` 内容升级为一次性打破陈旧终验门（设计目的，ADR-0104
  兼容性节披露）；同输入同指纹契约由测试钉住。
