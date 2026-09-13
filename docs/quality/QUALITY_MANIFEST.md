# Quality Manifest（自动生成）

> 本文件由 `python scripts/gen_quality_manifest.py` 从各 registry 与测试引用索引派生，请勿手改。唯一事实源：ToolRegistry / AlgorithmRegistry / CapabilityRegistry / ArtifactTypeRegistry / RecipeRegistry 与 tests/ 源码本身。

- Manifest 版本：**2**
- 内容指纹：`2754ba7fff200e45…`

## 总览

| section | total | 静态测试引用 | findings |
|---|---|---|---|
| tools | 327 | 0 | 338 |
| algorithms | 230 | 0 | 1 |
| capabilities | 153 | 0 | 0 |
| artifact_types | 21 | 0 | 21 |
| recipes | 164 | 164（conformance 由 workflow 闸保护） | 0 |

## 描述符富化闸（ADR-0103，可执行工具）

| field | coverage | threshold | status |
|---|---|---|---|
| side_effect | 100% | 95% | PASS（缺 0） |
| tags | 100% | 95% | PASS（缺 0） |
| latency_class | 100% | 95% | PASS（缺 0） |
| memory_class | 100% | 95% | PASS（缺 0） |
| capabilities | 100% | 95% | PASS（缺 0） |

**gate: PASS**（`total=327`）

## 行为化覆盖与 findings 棘轮（Quality V2）

- 工具行为证据：dispatch **0** / mention 0 / none 327（dispatch 覆盖率 0%）
- findings 棘轮：**FAIL**（dispatch 下限 151，当前 0；active waivers 0，过期 0，被豁免 findings 0 —— 豁免项仍在上方法量清单中可见）
  - 违规：`ARTIFACT_TYPE_UNTESTED` 当前 21 > 基线 0
  - 违规：`TOOL_DESTRUCTIVE_UNTESTED` 当前 11 > 基线 0
  - 违规：`TOOL_UNTESTED` 当前 327 > 基线 8

## Findings（派生线索，非缺陷判定）

> 静态引用 ≠ 行为覆盖。findings 只回答"哪里没有任何测试证据"，修复优先级需结合 02-coverage-risk-map 的风险分级。

### TOOL_UNTESTED（327）

- `add_marker`（medium）— registered tool without any static test reference
- `aggregate_dataset`（medium）— registered tool without any static test reference
- `alias_layer`（medium）— registered tool without any static test reference
- `analyze_vegetation_index`（medium）— registered tool without any static test reference
- `apply_layer_filter`（medium）— registered tool without any static test reference
- `apply_layer_style`（medium）— registered tool without any static test reference
- `apply_template`（medium）— registered tool without any static test reference
- `attribute_filter`（medium）— registered tool without any static test reference
- `audit_spatial_quality`（medium）— registered tool without any static test reference
- `band_correlation_table`（medium）— registered tool without any static test reference
- `batch_geocode_cn`（medium）— registered tool without any static test reference
- `bivariate_join_count`（medium）— registered tool without any static test reference
- `bivariate_local_moran`（medium）— registered tool without any static test reference
- `bivariate_moran`（medium）— registered tool without any static test reference
- `block_kriging_surface`（medium）— registered tool without any static test reference
- `buffer_analysis`（medium）— registered tool without any static test reference
- `cancel_execution_run`（medium）— registered tool without any static test reference
- `central_feature`（medium）— registered tool without any static test reference
- `clear_annotations`（medium）— registered tool without any static test reference
- `clip_layer`（medium）— registered tool without any static test reference
- `cloud_qc_basic`（medium）— registered tool without any static test reference
- `cokriging_lmc_surface`（medium）— registered tool without any static test reference
- `cokriging_surface`（medium）— registered tool without any static test reference
- `combine_map_theme`（medium）— registered tool without any static test reference
- `compile_workflow_semantics`（medium）— registered tool without any static test reference
- `compute_ndvi`（medium）— registered tool without any static test reference
- `compute_spectral_index`（medium）— registered tool without any static test reference
- `compute_terrain`（medium）— registered tool without any static test reference
- `compute_vegetation_index`（medium）— registered tool without any static test reference
- `connect_data_source`（medium）— registered tool without any static test reference
- `control_floating_chart`（medium）— registered tool without any static test reference
- `convert_raster_to_cog`（medium）— registered tool without any static test reference
- `convex_hull`（medium）— registered tool without any static test reference
- `cost_distance_analysis`（medium）— registered tool without any static test reference
- `create_3d_extrusion_map`（medium）— registered tool without any static test reference
- `create_new_skill`（high）— destructive/mutating tool without any test reference
- `create_thematic_map`（medium）— registered tool without any static test reference
- `cross_k_analysis`（medium）— registered tool without any static test reference
- `cross_pcf_analysis`（medium）— registered tool without any static test reference
- `dasymetric_reallocation`（medium）— registered tool without any static test reference
- `deep_explore`（medium）— registered tool without any static test reference
- `depression_fill`（medium）— registered tool without any static test reference
- `describe_artifact`（medium）— registered tool without any static test reference
- `describe_dataset`（medium）— registered tool without any static test reference
- `describe_workspace`（medium）— registered tool without any static test reference
- `detect_change_cva`（medium）— registered tool without any static test reference
- `detect_raster_change`（medium）— registered tool without any static test reference
- `detect_ratio_change`（medium）— registered tool without any static test reference
- `detect_vegetation_change`（medium）— registered tool without any static test reference
- `dinf_flow_analysis`（medium）— registered tool without any static test reference
- …另有 277 条，见 quality-manifest.json

### TOOL_DESTRUCTIVE_UNTESTED（11）

- `create_new_skill`（high）— requires_confirmation tool without any test reference
- `location_allocation`（high）— requires_confirmation tool without any test reference
- `manage_analysis_asset`（high）— requires_confirmation tool without any test reference
- `optimize_route`（high）— requires_confirmation tool without any test reference
- `refresh_skill_surface`（high）— requires_confirmation tool without any test reference
- `scenario_compare`（high）— requires_confirmation tool without any test reference
- `spatial_decision_v2`（high）— requires_confirmation tool without any test reference
- `spatial_reasoning`（high）— requires_confirmation tool without any test reference
- `spatiotemporal_hotspot`（high）— requires_confirmation tool without any test reference
- `temporal_raster`（high）— requires_confirmation tool without any test reference
- `what_if_simulate`（high）— requires_confirmation tool without any test reference

### TOOL_DESCRIPTOR_INCOMPLETE（0）

（无）

### CAPABILITY_NO_PRODUCER（0）

（无）

### CAPABILITY_NO_CONFORMANCE（0）

（无）

### ALGO_NO_CONFORMANCE（0）

（无）

### ALGO_HEAVY_NO_VARIANTS（1）

- `terrain.cost_distance`（medium）— memory_cost=high but no backend_variants scale windows

### ALGO_SEED_POLICY_CONFLICT（0）

（无）

### ALGO_PRODUCTION_NO_UNCERTAINTY（0）

（无）

### ARTIFACT_TYPE_UNTESTED（21）

- `admin_aggregate_table`（medium）— artifact type without any static test reference
- `admin_boundary_set`（medium）— artifact type without any static test reference
- `change_set`（medium）— artifact type without any static test reference
- `chart_spec`（medium）— artifact type without any static test reference
- `density_surface`（medium）— artifact type without any static test reference
- `feature_collection`（medium）— artifact type without any static test reference
- `grid_aggregate`（medium）— artifact type without any static test reference
- `hotspot_result`（medium）— artifact type without any static test reference
- `line_feature_set`（medium）— artifact type without any static test reference
- `network_graph`（medium）— artifact type without any static test reference
- `od_matrix`（medium）— artifact type without any static test reference
- `od_table`（medium）— artifact type without any static test reference
- `poi_feature_set`（medium）— artifact type without any static test reference
- `point_feature_set`（medium）— artifact type without any static test reference
- `polygon_feature_set`（medium）— artifact type without any static test reference
- `proximity_zone`（medium）— artifact type without any static test reference
- `raster_surface`（medium）— artifact type without any static test reference
- `remote_sensing_index`（medium）— artifact type without any static test reference
- `service_area`（medium）— artifact type without any static test reference
- `stats_table`（medium）— artifact type without any static test reference
- `terrain_surface`（medium）— artifact type without any static test reference

## 已知边界

- 本清单只做静态引用发现；行为正确性由各 lane（unit / oracle replay /
  cartography closed-loop / perf / chaos）保证，见 docs/quality/。
- recipes 的行为闸由 workflow conformance（test_workflow_guards）与
  gen_workflow_catalog 字节一致性闸保护，此处不重复展开。
