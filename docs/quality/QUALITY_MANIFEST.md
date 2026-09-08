# Quality Manifest（自动生成）

> 本文件由 `python scripts/gen_quality_manifest.py` 从各 registry 与测试引用索引派生，请勿手改。唯一事实源：ToolRegistry / AlgorithmRegistry / CapabilityRegistry / ArtifactTypeRegistry / RecipeRegistry 与 tests/ 源码本身。

- Manifest 版本：**1**
- 内容指纹：`a176ff71ffcb43b7…`

## 总览

| section | total | 静态测试引用 | findings |
|---|---|---|---|
| tools | 291 | 261 | 182 |
| algorithms | 181 | 146 | 43 |
| capabilities | 121 | 69 | 16 |
| artifact_types | 21 | 21 | 0 |
| recipes | 164 | 164（conformance 由 workflow 闸保护） | 0 |

## 描述符富化闸（ADR-0103，可执行工具）

| field | coverage | threshold | status |
|---|---|---|---|
| side_effect | 84% | 83% | PASS（缺 47） |
| tags | 84% | 83% | PASS（缺 47） |
| latency_class | 100% | 95% | PASS（缺 0） |
| memory_class | 100% | 95% | PASS（缺 0） |
| capabilities | 62% | 60% | PASS（缺 112） |

**gate: PASS**（`total=291`）

## Findings（派生线索，非缺陷判定）

> 静态引用 ≠ 行为覆盖。findings 只回答"哪里没有任何测试证据"，修复优先级需结合 02-coverage-risk-map 的风险分级。

### TOOL_UNTESTED（30）

- `apply_layer_style`（medium）— registered tool without any static test reference
- `cancel_execution_run`（medium）— registered tool without any static test reference
- `control_floating_chart`（medium）— registered tool without any static test reference
- `cross_pcf_analysis`（medium）— registered tool without any static test reference
- `describe_artifact`（medium）— registered tool without any static test reference
- `detect_change_cva`（medium）— registered tool without any static test reference
- `detect_ratio_change`（medium）— registered tool without any static test reference
- `execute_execution_plan`（medium）— registered tool without any static test reference
- `find_artifacts_by_role`（medium）— registered tool without any static test reference
- `geary_c`（medium）— registered tool without any static test reference
- `get_execution_run`（medium）— registered tool without any static test reference
- `get_lineage`（medium）— registered tool without any static test reference
- `ica_transform`（medium）— registered tool without any static test reference
- `interpolation_model_compare`（medium）— registered tool without any static test reference
- `list_workspace_snapshots`（medium）— registered tool without any static test reference
- `mantel_test_analysis`（medium）— registered tool without any static test reference
- `mgwr_regression`（medium）— registered tool without any static test reference
- `mnf_transform`（medium）— registered tool without any static test reference
- `quadrat_analysis`（medium）— registered tool without any static test reference
- `restore_workspace_snapshot`（medium）— registered tool without any static test reference
- `save_workspace_snapshot`（medium）— registered tool without any static test reference
- `search_datasets`（medium）— registered tool without any static test reference
- `space_time_k_analysis`（medium）— registered tool without any static test reference
- `temporal_changepoint`（medium）— registered tool without any static test reference
- `webgis_checkpoint`（medium）— registered tool without any static test reference
- `webgis_compile_maplibre`（medium）— registered tool without any static test reference
- `webgis_map_combine`（medium）— registered tool without any static test reference
- `webgis_rollback`（medium）— registered tool without any static test reference
- `webgis_validate`（medium）— registered tool without any static test reference
- `webgis_world_state`（medium）— registered tool without any static test reference

### TOOL_DESTRUCTIVE_UNTESTED（0）

（无）

### TOOL_DESCRIPTOR_INCOMPLETE（152）

- `add_marker`（low）— missing descriptor fields: capabilities
- `aggregate_dataset`（low）— missing descriptor fields: capabilities
- `alias_layer`（low）— missing descriptor fields: capabilities
- `analyze_vegetation_index`（low）— missing descriptor fields: capabilities
- `apply_layer_filter`（low）— missing descriptor fields: capabilities
- `apply_layer_style`（low）— missing descriptor fields: capabilities
- `apply_template`（low）— missing descriptor fields: capabilities
- `attribute_filter`（low）— missing descriptor fields: capabilities
- `audit_spatial_quality`（low）— missing descriptor fields: capabilities
- `band_correlation_table`（low）— missing descriptor fields: side_effect,tags
- `batch_geocode_cn`（low）— missing descriptor fields: capabilities
- `bivariate_join_count`（low）— missing descriptor fields: side_effect,tags
- `bivariate_local_moran`（low）— missing descriptor fields: side_effect,tags
- `block_kriging_surface`（low）— missing descriptor fields: side_effect,tags
- `cancel_execution_run`（low）— missing descriptor fields: capabilities
- `clear_annotations`（low）— missing descriptor fields: capabilities
- `cloud_qc_basic`（low）— missing descriptor fields: side_effect,tags
- `cokriging_surface`（low）— missing descriptor fields: side_effect,tags
- `combine_map_theme`（low）— missing descriptor fields: capabilities
- `connect_data_source`（low）— missing descriptor fields: capabilities
- `control_floating_chart`（low）— missing descriptor fields: side_effect,tags,capabilities
- `create_3d_extrusion_map`（low）— missing descriptor fields: capabilities
- `create_new_skill`（low）— missing descriptor fields: capabilities
- `create_thematic_map`（low）— missing descriptor fields: capabilities
- `deep_explore`（low）— missing descriptor fields: capabilities
- `describe_artifact`（low）— missing descriptor fields: side_effect,tags,capabilities
- `describe_dataset`（low）— missing descriptor fields: capabilities
- `detect_vegetation_change`（low）— missing descriptor fields: capabilities
- `directional_variogram_analysis`（low）— missing descriptor fields: side_effect,tags
- `display_layer`（low）— missing descriptor fields: capabilities
- `execute_execution_plan`（low）— missing descriptor fields: capabilities
- `execute_plan`（low）— missing descriptor fields: capabilities
- `export_batch_maps`（low）— missing descriptor fields: capabilities
- `export_thematic_map`（low）— missing descriptor fields: capabilities
- `extract_endmembers_vca`（low）— missing descriptor fields: side_effect,tags
- `fetch_sentinel`（low）— missing descriptor fields: capabilities
- `finalize_display`（low）— missing descriptor fields: capabilities
- `find_artifacts_by_role`（low）— missing descriptor fields: side_effect,tags,capabilities
- `fly_to_location`（low）— missing descriptor fields: capabilities
- `generate_analysis_report`（low）— missing descriptor fields: capabilities
- `generate_chart`（low）— missing descriptor fields: capabilities
- `generate_monitoring_report`（low）— missing descriptor fields: capabilities
- `geocode`（low）— missing descriptor fields: capabilities
- `geocode_cn`（low）— missing descriptor fields: capabilities
- `geodetector_ecological`（low）— missing descriptor fields: side_effect,tags
- `geodetector_risk`（low）— missing descriptor fields: side_effect,tags
- `get_execution_run`（low）— missing descriptor fields: capabilities
- `get_lineage`（low）— missing descriptor fields: side_effect,tags,capabilities
- `get_local_osm_catalog`（low）— missing descriptor fields: capabilities
- `get_local_stats_catalog`（low）— missing descriptor fields: capabilities
- …另有 102 条，见 quality-manifest.json

### CAPABILITY_NO_PRODUCER（0）

（无）

### CAPABILITY_NO_CONFORMANCE（16）

- `admin_boundary_query`（medium）— native capability whose producers declare no conformance tests
- `category_breakdown`（medium）— native capability whose producers declare no conformance tests
- `dataset_ingest`（medium）— native capability whose producers declare no conformance tests
- `external_route_planning`（medium）— native capability whose producers declare no conformance tests
- `federated_dataset_query`（medium）— native capability whose producers declare no conformance tests
- `geometry_centroid`（medium）— native capability whose producers declare no conformance tests
- `geometry_clip`（medium）— native capability whose producers declare no conformance tests
- `geometry_dissolve`（medium）— native capability whose producers declare no conformance tests
- `poi_query`（medium）— native capability whose producers declare no conformance tests
- `raster_cog_conversion`（medium）— native capability whose producers declare no conformance tests
- `raster_source`（medium）— native capability whose producers declare no conformance tests
- `spatial_join`（medium）— native capability whose producers declare no conformance tests
- `traffic_status`（medium）— native capability whose producers declare no conformance tests
- `transit_routing`（medium）— native capability whose producers declare no conformance tests
- `workspace_snapshot`（medium）— native capability whose producers declare no conformance tests
- `workspace_state_inspection`（medium）— native capability whose producers declare no conformance tests

### ALGO_NO_CONFORMANCE（20）

- `admin.boundary.local`（medium）— algorithm declares no conformance test node ids
- `admin.boundary_lookup`（medium）— algorithm declares no conformance test node ids
- `data.federated.chain`（medium）— algorithm declares no conformance test node ids
- `data.ingest.pipeline`（medium）— algorithm declares no conformance test node ids
- `geometry.center_statistics`（medium）— algorithm declares no conformance test node ids
- `geometry.clip`（medium）— algorithm declares no conformance test node ids
- `geometry.dissolve`（medium）— algorithm declares no conformance test node ids
- `geometry.spatial_join`（medium）— algorithm declares no conformance test node ids
- `network.isochrone`（medium）— algorithm declares no conformance test node ids
- `network.route_external_api`（medium）— algorithm declares no conformance test node ids
- `network.service_area.simple`（medium）— algorithm declares no conformance test node ids
- `network.traffic_status_external`（medium）— algorithm declares no conformance test node ids
- `network.transit_route_external`（medium）— algorithm declares no conformance test node ids
- `poi.area_search`（medium）— algorithm declares no conformance test node ids
- `poi.query.local`（medium）— algorithm declares no conformance test node ids
- `raster.cog.convert`（medium）— algorithm declares no conformance test node ids
- `raster.source.dem`（medium）— algorithm declares no conformance test node ids
- `stats.category.breakdown`（medium）— algorithm declares no conformance test node ids
- `workspace.inspection.readonly`（medium）— algorithm declares no conformance test node ids
- `workspace.snapshot.durable`（medium）— algorithm declares no conformance test node ids

### ALGO_HEAVY_NO_VARIANTS（23）

- `data.ingest.pipeline`（medium）— memory_cost=high but no backend_variants scale windows
- `interpolation.block_kriging`（medium）— memory_cost=high but no backend_variants scale windows
- `interpolation.cokriging`（medium）— memory_cost=high but no backend_variants scale windows
- `interpolation.idw`（medium）— memory_cost=high but no backend_variants scale windows
- `interpolation.indicator_kriging`（medium）— memory_cost=high but no backend_variants scale windows
- `interpolation.rbf`（medium）— memory_cost=high but no backend_variants scale windows
- `interpolation.regression_kriging`（medium）— memory_cost=high but no backend_variants scale windows
- `interpolation.universal_kriging`（medium）— memory_cost=high but no backend_variants scale windows
- `point_pattern.ripley_k_env`（medium）— memory_cost=high but no backend_variants scale windows
- `remote.ica`（medium）— memory_cost=high but no backend_variants scale windows
- `remote.mad_change`（medium）— memory_cost=high but no backend_variants scale windows
- `remote.mnf`（medium）— memory_cost=high but no backend_variants scale windows
- `remote.ndvi`（medium）— memory_cost=high but no backend_variants scale windows
- `remote.pca`（medium）— memory_cost=high but no backend_variants scale windows
- `sar.glcm_texture`（medium）— memory_cost=high but no backend_variants scale windows
- `sar.multitemporal_speckle`（medium）— memory_cost=high but no backend_variants scale windows
- `sar.speckle_filter`（medium）— memory_cost=high but no backend_variants scale windows
- `spatial.kde.contours`（medium）— memory_cost=high but no backend_variants scale windows
- `spatial.kde.surface`（medium）— memory_cost=high but no backend_variants scale windows
- `terrain.aspect`（medium）— memory_cost=high but no backend_variants scale windows
- `terrain.hillshade`（medium）— memory_cost=high but no backend_variants scale windows
- `terrain.sink_fill`（medium）— memory_cost=high but no backend_variants scale windows
- `terrain.slope`（medium）— memory_cost=high but no backend_variants scale windows

### ALGO_SEED_POLICY_CONFLICT（0）

（无）

### ALGO_PRODUCTION_NO_UNCERTAINTY（0）

（无）

### ARTIFACT_TYPE_UNTESTED（0）

（无）

## 已知边界

- 本清单只做静态引用发现；行为正确性由各 lane（unit / oracle replay /
  cartography closed-loop / perf / chaos）保证，见 docs/quality/。
- recipes 的行为闸由 workflow conformance（test_workflow_guards）与
  gen_workflow_catalog 字节一致性闸保护，此处不重复展开。
