# INTERFACE_CONTRACTS — vNext 接口冻结（实现前定稿，ADR-0282）

> 红线继承 goal-02：薄包装、registry.dispatch 单一执行真相、词表不复制。
> 全部 additive；既有键语义零漂移。

## 1. 模块布局

```
app/services/gis_harness/analysis_workflows/
├── contracts.py        # +envelope v2 字段/+step.semantic_contract/+binding.role_kind/+2 typed 码（同文件 additive）
├── envelope_facts.py   # 新：实测事实采集纯函数（几何类型/extent/semantics/raster 事实——有界扫描）
├── semantic_gate.py    # 新：typed 衔接门（plan 期 qualifier 披露 + run 期 FC/envelope 核验）
├── families.py         # +raster_zonal_summary 族；service_accessibility +nearest_facility/+accessibility_summary；admin 族分母字段解析
├── orchestrator.py     # +几何/分母字段/预算估计 plan 期裁决
├── executor.py         # +raster 直通/+semantic gate/+repeated 复用/+envelope v2 装配/science_validation
├── recompute.py        # FINGERPRINT_SCHEMA=2（typed 语义字段进指纹）
├── product_gate.py     # 新：ref 资格纯函数（partial≠final）
├── discovery.py        # 新：family→步骤/前置/预算/可制图性/next_steps 机读投影
└── state.py            # +science_validation 字段（additive）
app/tools/analysis_workflow_tools.py   # plan/status 输出 +discovery/预算估计/typed next_step（同文件 additive）
app/services/gis_harness/tools.py      # map_product +product_gate 钩子（最小 diff，独立 commit，GIS_AWF_PRODUCT_GATE 开关）
app/services/gis_harness/skills/library/core/analysis_workflows.yaml  # +raster_zonal_summary 过程包
```

## 2. 契约新增（pydantic）

### AnalysisArtifactEnvelope v2（additive 字段，默认=未知）
```python
schema_version: int = 2
geometry_types: Tuple[str, ...] = ()          # ≤4；{"Point","MultiPoint","LineString",...}
extent: Tuple[float, float, float, float] = ()  # [minx,miny,maxx,maxy]；空=未知
result_semantics: Dict[str, str] = {}          # ≤8：working_crs/density_unit/rate_unit/stat_denominator…
temporal: Dict[str, Any] = {}                  # 时间标识（year 等）
raster: Dict[str, Any] = {}                    # ≤8：nodata/resolution/band_count/dtype/valid_pixel_coverage
aggregation_basis: str = ""                    # "" |one_to_one|many_to_one_sum|m_n_join|zonal_mean|zonal_sum
provenance: Dict[str, str] = {}                # ≤6：descriptor_fp/method_version（摘要引用）
owner_session_id: str = ""
```

### AnalysisStepSpec（additive）
```python
semantic_contract: Dict[str, str] = {}   # 键⊆{crs_class, geometry_kind}；值∈crs_safety 词表/几何类
```

### DataRoleBinding（additive）
```python
role_kind: str = "vector"                # vector | raster | table
```

### 新 typed 码
`AWF_INPUT_SEMANTIC_MISMATCH`（+":<param>"）、`AWF_ROLE_GEOMETRY_MISMATCH`（+":<ROLE>"）；
复用既有 `AWF_BUDGET_EXCEEDED`（plan 期估计命中同码 + estimate 披露）。

### AGGREGATION_BASIS 词表
`("", "one_to_one", "many_to_one_sum", "m_n_join", "zonal_mean", "zonal_sum")`

## 3. 新族 raster_zonal_summary

| 步骤 | 工具 | 输入 | 关键参数 | optional |
|---|---|---|---|---|
| resample | raster_resample | raster_path=role:raster_index(raster) | target_resolution/resampling | True |
| zonal_stats | zonal_stats | geojson=role:boundary, raster_path=step:resample 或 role:raster_index | —（stats 集合默认工具内建） | False |

角色：raster_index(required, raster, block)、boundary(required, polygon, block)。
envelope：zonal 步 aggregation_basis="zonal_mean"；raster 事实进 envelope.raster；
visualization_hints: zonal_zones→administrative_choropleth。

## 4. network_proximity 新族（链③；R1 评审后定稿——与早期草案的差异已对齐 ADR D4）

| 步骤 | 工具 | 输入 | optional |
|---|---|---|---|
| nearest_facility | network_closest_facility | network=role:network, incidents=role:subject, facilities=role:facility | False |
| accessibility_summary | spatial_aggregate | points=step:nearest_facility, polygons=role:boundary | False |

角色：subject(需求点, required, point, block)/facility(required, point,
block)/network(required, block)/boundary(required, polygon, block)。
service_accessibility 的强化是 isochrone 步族默认 exclude_unreachable=True
（取代草案的 require_reachable 参数名）。

## 5. admin 族分母解析（行为兼容）

per_capita_rate.denominator = binding(denominator).qualifier.get("field", "population")；
qualifier.fields（列表）存在且目标列 ∉ → plan 期 typed skipped AWF_DENOMINATOR_MISSING。

## 6. 产品门

`product_gate.assess_ref_eligibility(states: Dict[workflow_id, WorkflowRunState], ref) ->
{eligible: bool, run_status: str, reason_code: str}`；仅当 ref 命中某 envelope.ref 且
该 workflow status ∉ {satisfied} 或 science_validation.final_gate=="blocked" 时不合格。
`GIS_AWF_PRODUCT_GATE=0` 回退。

## 7. discovery 投影

`discovery.family_catalog(family=None) -> {families: [{family, label_zh, roles, steps:
[{step_id, tool, capability, preconditions, params, unit_contract, mappable_output(bool),
budget_hint}], next_steps_on_blocked: [{reason_code, action}]}]}`（有界，零 IO）。
plan 工具 blocked 裁决附 `next_step: {action, hint}`（结构化，非自由文本）。

## 8. 明确不做

不新增 Pi 工具（复用既有 4 个）；零前端改动（catalogue 不变）；不改
ToolDispatchService/MapSpec/Pi bridge；不修 review issues 同文件行为；
不调预算上限（估计只披露）；不做栅格 mask 新工具（zonal 窗口读已隐式掩膜，
hints 如实说明）。
