# Spec: GIS Harness — Cartography Recipes & Composable Map Products

> 状态：Implemented（2026-08）。实现文档见 `docs/gis-harness.md`。
> 上位契约：`specs/mapspec-cartography-harness-spec.md`（MapSpec 双写、
> ref_id、评估面）；`docs/cartographic-closed-loop.md`（L1–L5 证据分层）。

## 1. 目标与非目标

**目标**：在 Pi / ToolDispatchService / MapSpec / PiAgentHarness 既有架构上
补齐 GIS 领域智能层——typed intent、确定性 recipe 选择与资格复检、可组合
制图组件、完整地图产品规划与证据。「分布」不再等价于单个工具调用。

**非目标**：不新建第二套 MapSpec / dispatcher / runtime state；不修改 Pi
本体；不删除既有 compatibility 行为（渐进迁移）。

## 2. 契约

### 2.1 热力半径（P0）

- 视觉热力：`radius_px`（MapLibre 屏幕像素，默认 30，clamp [4,80]）。
- 分析密度：`bandwidth_m`（米，默认 1000；raster/grid `sigma` 的真实输入）。
- legacy `radius`：仅在 `app/lib/cartography/heatmap_contract.py`（前端镜像
  `renderer.ts::resolveHeatmapRadiusPx`）归一化——`bandwidth_m = radius`；
  视觉半径 4–60 历史窗口 px 直通，超窗默认 30px + 迁移警示。
  **不变量：米值绝不被当作像素消费。**
- MapSpec 热力层携带 `heatmap` 兄弟键（radius_px/bandwidth_m/radius_source）
  供审计与 HUD 投影（`_runtime_patch → style.radius_px`）；paint 保持
  MapLibre 原生 `heatmap-*` 表达式（headless 编译器显式透传该方言）。

### 2.2 MapRequestIntent（`gis_harness/intent.py`）

- typed / serializable / deterministic；resolver 规则序 = 特异性，先命中先
  停；每次命中记录 `matched_rules`；未识别显式记 `assumptions`。
- LLM hint 白名单合并（`merge_intent_hints`），覆盖全部记录进
  `hint_applied`；`analytical_density` 不可被 hint 降级（视觉热力不是定量
  证据）。
- 语义护栏：『各区…数量』→ administrative_statistic（choropleth 优先）；
  『每平方公里密度』→ analytical_density；『给我看看』→ simple_view。

### 2.3 CartographyRecipe（`gis_harness/recipes.py`）

- schema：id/name/intent_tasks/intent_cartography/required|allowed_geometry/
  eligibility（check_points + min_points、requires_geometry、requires_fields）/
  preferred|optional_analysis（能力 id）/primary|secondary_cartography/
  default_components/fallbacks（when/reason_code/use/disable）/
  export_profile/priority。
- 注册表 O(1) indexed；候选排序确定性（task 命中 > cartography 交集 >
  priority > id）。
- **eligibility / fallback 必须代码侧确定性检查**（profile 输入）；
  数据回来后必须复检（`finalize_with_profile`），回退记录
  `{from, to, reason_code, evidence}`。
- **阈值同源**：visual_heatmap 的点数阈值由调用方注入
  （`HEATMAP_MIN_POINTS` 设置），recipe 不硬编码——与工具/converter 守卫
  不漂移。
- **主体几何 ≠ 产品几何**：recipe 级 `required_geometry` 只约束「主数据就是
  该几何」的 recipe（如 poi/hotspot 要求点）；`administrative_choropleth`
  的主体是被统计点数据，行政面来自 admin 能力——其资格在绑定期把关
  （面状 ref / 已授权 fill 层才挂 choropleth），不在主数据 profile 上误判。
- 首批：poi_distribution_overview、point_density、administrative_choropleth、
  categorical_distribution、hotspot_analysis、proximity_analysis、
  accessibility_analysis、raster_distribution。

### 2.4 CartographyComponent（`gis_harness/components.py`）

- 统一 schema：id/type/enabled/position/priority/style/options/compatibility；
  15 个首批类型。
- legend（离散/分级）与 continuous_colorbar（连续）是两种组件，按主表达
  选择，不混用。
- 局部突变（`mutate_component` / `webgis_component_update`）只动命中组件；
  突变证据断言图层数不变（不触发数据重查/重分析）。
- 进入 MapSpec：`layout.components`（lifecycle 事务 + priority/id 稳定排序 +
  非法条目原子拒绝）。live（map-spec-chrome.tsx）与 export（exporter.ts
  默认值）共用同一份；显式导出请求参数 > spec 组件 > 内置默认。

### 2.5 MapProductTemplate / MapProductPlan

- 模板 = recipe + 图层角色 + 组件 + 输出物（期望产品，非工具脚本）；能力
  →工具解析在执行期（`CAPABILITY_TOOLS` + 注册表存在性检查）。
- 计划两阶段：draft（`plan_from_intent`）→ finalized
  （`finalize_with_profile`：eligibility 复检、图层裁决、终稿组件、完整
  性评估）。plan_id 决定性（query+recipe 哈希）——可回放。

### 2.6 工具面

- `webgis_map_intent`：意图 + 候选 + 计划骨架（tier-2, statistics+report）。
- `webgis_map_product`：资格复检 + 角色绑定（类型确定性映射）+ 缺层补齐
  （既有 converter 授权）+ 组件落 MapSpec + 产品证据。`recipe_id`/`task_hint`
  参数重放意图阶段的纠偏——**两阶段共用同一份计划**（plan 连续性）。
- `webgis_component_update`：组件局部突变（tier-2, report）。
- `webgis_map_product` 的 `primary_ref`/`overlay_refs` 在 registry 解引用
  skip-list（ref 游标不被内联为大 payload）；全量数据只在确需补层时拉取。

### 2.7 证据（PiAgentHarness 扩展）

- `ToolCallEvidence.map_product`（MapProductEvidence：intent/recipe/
  eligibility/fallback/component/completeness，有界转录）。
- 维度 `MapProductCompleteness`（阈值 80）：仅有产品证据时评估；无证据
  run 豁免（not_applicable_exempt）；`require_map_product=True` 收紧。
- **缺证据绝不为 PASS**（既有 not_evaluated 原则不变）。

## 3. 测试面

- `tests/unit/gis_harness/`：intent（Golden A–H 意图层）、planner（Golden
  A–H 计划层）、components（schema/突变/MapSpec 集成）、tools（工具契约）。
- `tests/unit/test_heatmap_contract.py`：radius 契约（1000m ≠ 1000px 全链
  路证明）。
- 前端：`map-spec-chrome.test.tsx`（组件渲染/禁用/变体/竖向色条）、
  `adapter.test.ts`（radius_px 契约）、`compiler.test.ts`（原生键透传）、
  `heatmapCommands.test.ts` / `renderer.test.ts`（radius_px 传递）。

## 4. 兼容性

- legacy radius 归一化消化（警示 + 迁移路径）；`layout.components` 可选键，
  旧 spec / 旧 session 行为不变；template ids / 工具名 / 导出请求不变。
