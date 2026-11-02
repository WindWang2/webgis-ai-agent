# BASELINE — 空间语义与量纲可信契约（GOAL 01）

基线：master@6c5e71c2c（2026-10-31 18:54 +0800，PR #1875 合并后）。
调查方式：3 个只读探查 agent + 主 agent 亲读核心文件（行号均为该 commit 实测）。

## 1. 已有实现（按子功能）

### 1.1 CRS 分类与投影推荐（复用，不重写）
- `app/lib/gis/crs_safety.py`
  - `classify_crs()` :114-163 — geographic/projected/projected_local_metric/unknown 四分类；词法优先 + pyproj 兜底 + **实测尺度失真门**（`_local_metric_scale_ok` :59-111，≤1%）；lru_cache。
  - `recommend_metric_crs(bbox)` :166-187 — 纯计算 UTM/UPS 推荐。
  - `crs_class_allows()` :190-207 — resolver 硬门谓词。
  - 消费方：`algorithm_resolver.py:209-218`（科学门 1）、`scientific_preconditions.py:133-165`、`aggregation.py:298-301`、`dataset_profile.py:197` 等 7 处。
- 科学 oracle 已冻结：`tests/science_oracles/data/crs_units.json`（classify_crs 5 cases）。

### 1.2 字段级量纲契约（复用）
- `app/lib/gis/measurement.py` — `CANONICAL_UNITS` :100-116（m/km/m²/km²/公顷/degrees…）、
  `MeasurementKind`（含 density）:60-73、`derive_field_semantics` :435-562（含
  `CHECK_DEGREE_LIKE_METRIC` :524-535、`CHECK_UNIT_DIMENSION_MISMATCH` :517-523）、
  `legend_unit_display` :659。版本化 to_dict/from_dict fail-closed 先例 :196-263。
- 消费方：field_resolver / symbology / cartography 工具 / dataset_semantics。

### 1.3 MethodUseContract（算法侧静态目录投影，schema v1）
- `app/lib/gis/method_use_contract.py` — `crs_class`/`unit_requirements`/`parameters[].unit`
  （PARAM_UNIT_VOCABULARY，parameter_contracts.py:28-31）；零 IO 只读投影 + findings。
- 消费方：scene3d verdict、analysis_workflows evidence、CLI（scripts/inspect_method_contract.py）。

### 1.4 跨 180° 纯函数套件（复用）
- `app/lib/gis/longitude.py` — detect/split/normalize/describe :31-298。
  执行路径未消费几何级拆分（见缺口 G7）。

### 1.5 执行链 CRS 纪律（现状强项）
- `app/lib/geo_processor/core.py`：`extract_declared_crs` :201-222（GeoJSON crs member 单一真相）、
  `gdf_from_features` :246-285（GCJ/BD 声明归一 WGS84 :255-262；已投影输入重投影回 4326 工作帧 :281-282）、
  `to_utm_gdf_with_note` :384+（自动 UTM/极区/跨 AM 选带 rewrap :519-536；crs_note 披露）。
- `buffer_smart`（geo_processor/geometry.py:17）非米投影轴因子换算 #524/#588；
  `multi_ring_buffer`（geometry_ops.py:238）同契约。
- `aggregate_with_denominator`（aggregation.py:346-552）— 分母三族 field/area/count、
  `_metric_zone_area_m2` :286-336（local-metric 直算/geographic→UTM/wide→测地）、零分母 rate=None、
  rate_unit="per_m2" 诚实标注、count 分母标 count_ratio_not_rate :529-536。
- `zonal_statistics`（raster_ops.py:53-125）显式重投影 + inf 拦截。
- GCJ-02/BD-09 数学：`app/utils/coord_transform.py` :87-227（含 `normalize_chinese_crs` 单一权威）。

### 1.6 数据准备收据（#1868，复用其词表）
- `app/services/data_preparation/spatial_receipt.py` — `SpatialInspectionReceipt`
  （srs/crs_source/axis_order_guessed/coordinate_range :106-109）、
  `_axis_order_check` :184-240、`REPROJECT_ON_ASSUMED_CRS` 闸（plan.py:319-322）。
- crs_source 四态：declared/source/assumed/rfc7946_default。

### 1.7 分析工作流单位契约（#1869，组合面）
- `analysis_workflows/contracts.py` — `UNIT_VOCABULARY=("m","km","min","h","unitless")` :48、
  `ScienceContext.metric_crs_policy="auto_utm"` :57-67、`AnalysisArtifactEnvelope.crs/units` :150-151、
  `AWF_UNIT_CONTRACT_VIOLATION` fail-closed :290-294。

### 1.8 工具执行入口
- `app/services/tool_dispatch_service.py` — `dispatch()` :308-1511 唯一入口；
  闸序列 dedup→capability bind→Action IR→guardrails→断路→registry 执行；
  ref 铸造/血缘 :1124-1350；typed error 全走 `ToolDispatchResult(status="error")`。
- ref 指纹：dedup（normalize_tool_args sorted-json）、analysis_reuse
  （compute_analysis_key/snapshot_input_shapes/snapshot_ref_revisions）、
  capture_arg_lineage_refs、descriptor_fingerprint。
- 工具注册：`app/tools/__init__.py` _TOOL_MODULES 列表（单行增量注册模式）。

## 2. 真实缺口（GOAL 01 的实施面，全部有 file:line 证据）

| # | 缺口 | 证据 |
|---|---|---|
| G1 | 执行期无实例级空间语义契约：working_crs/换算链/分母/轴序/归一事实散落各工具 evidence dict，无统一 versioned 契约 | method_use_contract.py:46-77（静态）vs buffer_smart evidence（geometry.py:116-131，运行时） |
| G2 | geocompute 管线重建 FC 丢 crs member（度/米混淆现行通路） | geocompute/ops.py:461, 498-504 |
| G3 | voronoi/convex_hull 面积输出硬编码 /1e6（非米投影输入时 km² 错 10.76×） | geometry_ops.py:125,204,226（multi_ring_buffer :325 有轴因子修正，这两处没有） |
| G4 | 3857 等非局部度量已投影输入直接在其上度量；crs_safety 判定不进执行路径 | core.py:505-506 + crs_safety.py:131 |
| G5 | 未声明 GCJ-02 数据无检测提示（偏移静默进 join/buffer） | core.py:236-241 只查 WGS84 包络越界 |
| G6 | KDE 密度值无单位/分母标注；kde_contours unit 自由串 | density.py:423,483,573 |
| G7 | 执行路径无几何级 AM 拆分；选带 rewrap 与 longitude.py 两套逻辑 | core.py:519-536 vs longitude.py:173 无调用 |
| G8 | 多带/大跨度距离类计算无失真披露（面积有测地兜底、距离没有） | aggregation.py:263-283 仅面积 |
| G9 | lat/lon 轴序翻转无探测（always_xy 假设输入即 x,y） | coord_transform.py:468 |
| G10 | 意图层距离值是原始字符串（"500米"）未归一；RequirementIR distance_m 字段存在但 build 链从不赋值 | intent_semantic.py:991-993；requirement_ir/build.py L92-96（不填 distance_m） |
| G11 | 密度分母"查询期语境"契约空位（field_resolver 只有提示文案） | field_resolver.py:416-431；measurement.py:150-152 注释明示留给查询期 |
| G12 | MapSpec 单位链路半通不通：legend_spec.unit 是开放 dict 可选键；后端 derive_legend_items（render_scene.py:249-254）与前端 LegendModel（legend-model.ts:32-38）均无 unit 投影；create_thematic_map 的 derive_field_semantics crs 硬编码 "EPSG:4326"（cartography.py:359） | render_scene.py / legend-model.ts / cartography.py |
| G13 | scope（AOI）无 CRS/几何/bbox 载体；无 AOI×数据 extent 相交校验 | intent.py:120-123；requirement_ir/contracts.py:200-204 |

## 3. 端到端调用链追踪（3 个代表场景）

### 场景 A：成都小学 500m 覆盖（buffer）
自然语言 → `intent.py:resolve_map_request_intent`（scope=成都/province-city，
距离只进 slots.constraints 原始串，intent_semantic.py:991）→ planner →
`tool_dispatch_service.dispatch("buffer_analysis")` → `app/tools/spatial.py:344`
（BufferAnalysisArgs distance>0/unit∈{m,km} :268-271）→ `spatial_analyzer.py:71`
→ `buffer_smart` → `to_utm_gdf_with_note`（auto-UTM/GCJ 归一/crs_note）→ 米制
buffer → 回 4326 + `declare_crs` + quality evidence → dispatch 铸 ref →
MapSpec layer_upsert（dispatch :1352-1405）。
缺口：preflight 无 typed 契约；无 CRS 投影数据走 warn-then-4326；3857 直接缓冲。

### 场景 B：各区每平方公里小学密度（choropleth）
→ intent（density 语义识别 intent_semantic.py:513/656）→ 规划期
`data_qualification._unit_dimension_guard` :319-367（字段级量纲闸）→
`spatial_aggregate`（advanced_spatial.py）→ `aggregate_with_denominator`
（aggregation.py:346：面积分母 `_metric_zone_area_m2`，rate_unit=per_m2）→
`create_thematic_map`（cartography.py:321-367，legend unit user-wins>弱证据推导，
crs 硬编码 4326）→ legend_spec["unit"]（开放键）→ 前端 colorbar/floating-legend
显示、graduated 图例卡不显示（G12）。
缺口：km² 换算链与"每 km²"口径无 typed 记账；per_km² → legend 单位链路断。

### 场景 C：BD-09 点位 × WGS84 边界空间连接
→ `spatial_join`（spatial_analyzer.py:436-467）→ 两层各自 `gdf_from_features`
（声明 bd09 → 逐几何归一 WGS84，audit #813）→ 右层对齐左层 CRS → gpd.sjoin。
缺口：归一事实只进 log 不进 join 结果 evidence；未声明 GCJ（G5）无警示；
跨帧 CRS 声明矛盾无 typed 拒绝。

## 4. 现有校验点清单（17 项，file:line 见探查报告）

规划期 5（algorithm_resolver CRS 门/scientific_preconditions/量纲闸/语义角色闸/
分母强证据）+ inspect 期 3（CRS_MISSING 质检/轴序嫌疑/assumed 禁重投影）+
意图期 4（AOI 卫生/AOI 澄清/密度话术/距离抽取）+ 执行前 4（guardrails L1 格式/
capability 拒绝/ref 指纹防陈旧复用/dedup）+ 事后 1（raster_guard）。
**结论：执行层（dispatch 前）没有 CRS/单位/范围/分母统一 preflight——这正是本任务的位置。**

## 5. 允许改动范围

- 新增（独占）：`app/services/gis_harness/spatial_semantics/`（全部新文件）。
- 最小适配：`app/tools/spatial.py`（buffer_analysis/spatial_join 适配层）、
  `app/tools/advanced_spatial.py`（spatial_aggregate/zonal_stats 适配层）、
  `app/tools/__init__.py` + `app/tools/categories.py`（各 1 行注册）、
  `app/services/geocompute/ops.py`（G2 crs member 保留修复）、
  `app/tools/cartography.py`（G12 crs 硬编码改真实 crs）、
  frontend graduated 图例 unit 显示（最小）、docs/adr + gis-harness.md 增补。
- 禁改：data_fabric/adapters/、时序 MapUI、`frontend/lib/map-kit/symbol-law.ts`、
  `app/lib/harness/replay/`、analysis_workflows 内部（只组合不重写）、
  measurement.py/crs_safety.py 词表（只引用不扩）。
