# 能力目录（自动生成 · ADR-0181）

> **本文件由能力图派生，请勿手工编辑。** 事实源：
> `app/lib/gis/capability_registry.py`（capability 词表）、
> `app/services/gis_harness/capability_graph.py`（派生图 + 结构审计）、
> recipes / product_templates / component_registry / data_fabric
> （provider 四段）。再生成：`python scripts/gen_capability_catalog.py`。
> 治理语义：本目录披露 warning 级结构发现（孤儿 / 环 / 不可达 /
> 无消费者 / 弃用暴露）——「先可观测，再逐段收紧」；error 级闸仍在
> `registry_validation.validate_gis_library`（零容忍）。
> 确定性口径：目录投影**运行时合并图**（含 modelops 本地注册模型）——
> 本机存在本地注册模型时重生成的字节可能与入库版不同，以 CI（空
> modelops 注册面）为入库真相；staleness 闸按输入指纹判定，不受此影响。

## 总览

- capability 词表：153 条（图内 153 节点）
- 图节点 962 / 边 3133 / graph fingerprint `74aca650f00c328d`
- provider 面：algorithm 230，component 20，model 10，provider(adapters) 15，template 8，tool 332，workflow(recipes) 164
- 域分布：`general` 71，`network` 14，`platform` 16，`raster` 49，`statistics` 1，`temporal` 2

## Capability 词表

| id | domain | category | status | offline | deterministic | fallbacks | incompatible |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `accessibility` | network | network | native | — | yes | — | — |
| `admin_aggregation` | general | analysis | native | — | yes | — | — |
| `admin_boundary_query` | general | data_access | native | — | yes | — | — |
| `analytical_density` | general | density | native | — | no | — | — |
| `areal_interpolation` | general | analysis | native | — | yes | — | — |
| `band_math` | raster | raster | native | — | yes | — | — |
| `band_statistics` | raster | raster | native | — | yes | — | — |
| `bivariate_local_moran` | general | statistics | native | — | yes | — | — |
| `bivariate_morans_i` | general | statistics | native | — | yes | — | — |
| `block_kriging` | general | analysis | native | — | yes | — | — |
| `category_breakdown` | general | statistics | native | — | yes | — | — |
| `change_detection` | general | analysis | native | — | yes | — | — |
| `closest_facility` | network | network | native | — | yes | — | — |
| `cloud_qc_advisory` | raster | raster | native | — | yes | — | — |
| `cokriging` | general | analysis | native | — | yes | — | — |
| `convex_hull` | general | analysis | native | — | yes | — | — |
| `cost_distance_analysis` | raster | raster | native | — | yes | — | — |
| `cross_k_function` | general | density | native | — | yes | — | — |
| `cross_pair_correlation` | general | density | native | — | yes | — | — |
| `crs_transformation` | platform | platform | planned | — | yes | — | — |
| `data_source_pipeline` | platform | platform | planned | — | yes | — | — |
| `dataset_ingest` | general | data_access | native | — | yes | — | — |
| `dataset_profiling_quality` | platform | platform | planned | — | yes | — | — |
| `density_surface` | general | density | native | — | no | grid_binning | — |
| `directional_distribution_analysis` | platform | platform | planned | — | yes | — | — |
| `emerging_hotspot_analysis` | general | statistics | native | — | yes | — | — |
| `endmember_extraction` | raster | raster | native | — | yes | — | — |
| `external_route_planning` | network | network | native | — | no | — | — |
| `federated_dataset_query` | general | data_access | native | — | yes | — | — |
| `general_g` | general | statistics | native | — | yes | — | — |
| `geocoding` | platform | platform | planned | — | no | — | — |
| `geographical_detector` | general | statistics | native | — | yes | — | — |
| `geometry_buffer` | general | analysis | native | — | yes | — | — |
| `geometry_centroid` | general | analysis | native | — | yes | — | — |
| `geometry_clip` | general | analysis | native | — | yes | — | — |
| `geometry_dissolve` | general | analysis | native | — | yes | — | — |
| `geometry_overlay` | general | analysis | native | — | yes | — | — |
| `geostatistical_simulation` | general | analysis | native | — | yes | — | — |
| `getis_ord_gi_star` | general | statistics | native | — | yes | — | — |
| `global_gearys_c` | general | statistics | native | — | yes | — | — |
| `global_morans_i` | general | statistics | native | — | yes | — | — |
| `gravity_accessibility` | network | network | native | — | yes | — | — |
| `grid_binning` | general | density | native | — | yes | density_surface | — |
| `gwr` | general | statistics | native | — | yes | — | — |
| `habitat_suitability` | general | analysis | native | — | yes | — | — |
| `hotspot` | general | statistics | native | — | yes | — | — |
| `ica_transform` | raster | raster | native | — | yes | — | — |
| `image_segmentation` | raster | raster | native | — | yes | — | — |
| `indicator_kriging` | general | analysis | native | — | yes | — | — |
| `interpolation_model_selection` | general | analysis | native | — | yes | — | — |
| `join_count_statistics` | general | statistics | native | — | yes | — | — |
| `kde_density` | general | density | native | — | no | — | — |
| `landscape_metrics` | raster | raster | native | — | yes | — | — |
| `layer_display_control` | platform | platform | planned | — | yes | — | — |
| `least_cost_path_analysis` | raster | raster | native | — | yes | — | — |
| `local_data_query` | platform | platform | planned | — | yes | — | — |
| `local_gearys_c` | general | statistics | native | — | yes | — | — |
| `local_join_count` | general | statistics | native | — | yes | — | — |
| `local_morans_i` | general | statistics | native | — | yes | — | — |
| `location_allocation` | network | network | native | — | yes | — | — |
| `mantel_test` | general | density | native | — | yes | — | — |
| `map_annotation_measurement` | platform | platform | planned | — | yes | — | — |
| `map_export_publishing` | platform | platform | planned | — | yes | — | — |
| `map_viewport_control` | platform | platform | planned | — | yes | — | — |
| `mcda_evaluation` | general | analysis | native | — | yes | — | — |
| `meta_tool_surface` | platform | platform | planned | — | no | — | — |
| `mnf_transform` | raster | raster | native | — | yes | — | — |
| `model_change_detection` | raster | analysis | native | — | yes | — | — |
| `model_embedding` | raster | analysis | native | — | yes | — | — |
| `model_image_segmentation` | raster | analysis | native | — | yes | — | — |
| `model_instance_segmentation` | raster | analysis | native | — | yes | — | — |
| `model_object_detection` | raster | analysis | native | — | yes | — | — |
| `model_super_resolution` | raster | analysis | native | — | yes | — | — |
| `model_temporal_classification` | temporal | analysis | native | — | yes | — | — |
| `model_temporal_forecast` | temporal | analysis | native | — | yes | — | — |
| `multi_ring_buffer` | general | analysis | native | — | yes | — | — |
| `ndvi` | raster | raster | native | — | yes | — | — |
| `nearest_neighbor_functions` | general | density | native | — | yes | — | — |
| `network_centrality` | network | network | native | — | yes | — | — |
| `od_flow_mapping` | network | network | native | — | yes | — | — |
| `od_matrix` | network | network | native | — | yes | — | — |
| `pair_correlation_function` | general | density | native | — | yes | — | — |
| `plan_workflow_orchestration` | platform | platform | planned | — | yes | — | — |
| `poi_query` | general | data_access | native | — | yes | — | — |
| `point_pattern_analysis` | general | density | native | — | yes | — | — |
| `point_profile` | general | statistics | native | — | yes | — | — |
| `proximity_buffer` | general | analysis | native | — | yes | — | — |
| `radiometric_normalization` | raster | raster | native | — | yes | — | — |
| `raster_change_detection` | raster | raster | native | — | yes | — | — |
| `raster_cog_conversion` | raster | raster | native | — | yes | — | — |
| `raster_dimensionality_reduction` | raster | raster | native | — | yes | — | — |
| `raster_reclassify` | raster | raster | native | — | yes | — | — |
| `raster_resample` | raster | raster | native | — | yes | — | — |
| `raster_source` | raster | raster | native | — | yes | — | — |
| `rate_aggregation` | general | analysis | native | — | yes | — | — |
| `rate_smoothing` | general | statistics | native | — | yes | — | — |
| `regression_kriging` | general | analysis | native | — | yes | — | — |
| `report_charting` | platform | platform | planned | — | yes | — | — |
| `route_optimization` | network | network | native | — | yes | — | — |
| `rx_anomaly_detection` | raster | raster | native | — | yes | — | — |
| `sar_analysis` | raster | raster | native | — | yes | — | — |
| `sar_coherence` | raster | raster | native | — | yes | — | — |
| `sar_radiometric_calibration` | raster | raster | native | — | yes | — | — |
| `sar_speckle_filtering` | raster | raster | native | — | yes | — | — |
| `sar_terrain_geometry_correction` | raster | raster | native | — | yes | — | — |
| `sar_texture` | raster | raster | native | — | yes | — | — |
| `scenario_simulation` | platform | platform | planned | — | no | — | — |
| `service_area` | network | network | native | — | yes | — | — |
| `shortest_path` | network | network | native | — | yes | — | — |
| `space_time_interaction` | general | density | native | — | yes | — | — |
| `space_time_k_function` | general | density | native | — | yes | — | — |
| `spatial_interaction` | network | network | native | — | yes | — | — |
| `spatial_interpolation` | general | analysis | native | — | yes | — | — |
| `spatial_join` | general | analysis | native | — | yes | — | — |
| `spatial_regression` | general | statistics | native | — | yes | — | — |
| `spatial_sampling` | general | analysis | native | — | yes | — | — |
| `spatial_weights_diagnostics` | general | statistics | native | — | yes | — | — |
| `spatiotemporal_clustering` | general | statistics | native | — | yes | — | — |
| `spatiotemporal_interpolation` | general | analysis | native | — | yes | — | — |
| `spectral_index` | raster | raster | native | — | yes | — | — |
| `spectral_target_detection` | raster | raster | native | — | yes | — | — |
| `spectral_unmixing` | raster | raster | native | — | yes | — | — |
| `tasseled_cap_transformation` | raster | raster | native | — | yes | — | — |
| `temporal_aggregate` | general | statistics | native | — | yes | — | — |
| `temporal_change_point` | general | analysis | native | — | yes | — | — |
| `temporal_composite` | raster | raster | native | — | yes | — | — |
| `temporal_feature_extraction` | raster | raster | native | — | yes | — | — |
| `temporal_filtering` | platform | platform | planned | — | yes | — | — |
| `temporal_profile` | general | statistics | native | — | yes | — | — |
| `temporal_smoothing` | general | analysis | native | — | yes | — | — |
| `temporal_trend` | statistics | analysis | native | — | yes | — | — |
| `terrain_aspect` | raster | raster | native | — | yes | — | — |
| `terrain_contours` | raster | raster | native | — | yes | — | — |
| `terrain_derivatives` | raster | raster | native | — | yes | — | — |
| `terrain_geomorphometry` | raster | raster | native | — | yes | — | — |
| `terrain_hillshade` | raster | raster | native | — | yes | — | — |
| `terrain_hydrology` | raster | raster | native | — | yes | — | — |
| `terrain_hydrology_advanced` | raster | raster | native | — | yes | — | — |
| `terrain_sky_view` | raster | raster | native | — | yes | — | — |
| `terrain_slope` | raster | raster | native | — | yes | — | — |
| `terrain_viewshed` | raster | raster | native | — | yes | — | — |
| `terrain_wetness_indices` | raster | raster | native | — | yes | — | — |
| `thematic_cartography` | platform | platform | planned | — | yes | — | — |
| `traffic_status` | network | network | native | — | no | — | — |
| `transit_routing` | network | network | native | — | no | — | — |
| `trend_surface` | general | analysis | native | — | yes | — | — |
| `triangulation_interpolation` | general | analysis | native | — | yes | — | — |
| `variogram_analysis` | general | analysis | native | — | yes | — | — |
| `voronoi_tessellation` | general | analysis | native | — | yes | — | — |
| `weights_sensitivity` | general | statistics | native | — | yes | — | — |
| `workspace_snapshot` | general | data_access | native | — | no | — | — |
| `workspace_state_inspection` | general | data_access | native | — | yes | — | — |
| `zonal_statistics` | raster | raster | native | — | yes | — | — |

## 结构审计发现（warning 级 · 治理清单）

| orphan_capability | cycle_detected | unreachable_tool | artifact_no_consumer | exposes_deprecated_tool |
| --- | --- | --- | --- | --- |
| 64 | 1 | 61 | 5 | 0 |

### orphan_capability（64）

- capability accessibility has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability areal_interpolation has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability band_statistics has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability bivariate_local_moran has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability bivariate_morans_i has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability block_kriging has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability change_detection has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability cloud_qc_advisory has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability cokriging has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability cost_distance_analysis has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability cross_k_function has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability cross_pair_correlation has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability emerging_hotspot_analysis has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability endmember_extraction has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability geographical_detector has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability geometry_centroid has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability geometry_dissolve has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability geostatistical_simulation has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability gravity_accessibility has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability gwr has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability habitat_suitability has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability ica_transform has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability indicator_kriging has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability interpolation_model_selection has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability join_count_statistics has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability landscape_metrics has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability least_cost_path_analysis has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability local_gearys_c has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability local_join_count has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability mantel_test has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability mnf_transform has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability nearest_neighbor_functions has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability network_centrality has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability pair_correlation_function has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability radiometric_normalization has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability raster_dimensionality_reduction has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability raster_resample has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability rate_smoothing has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability regression_kriging has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability rx_anomaly_detection has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability sar_coherence has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability sar_terrain_geometry_correction has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability sar_texture has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability space_time_interaction has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability space_time_k_function has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability spatial_interaction has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability spatial_regression has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability spatial_sampling has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability spatial_weights_diagnostics has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability spatiotemporal_interpolation has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability spectral_target_detection has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability spectral_unmixing has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability tasseled_cap_transformation has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability temporal_composite has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability temporal_feature_extraction has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability temporal_smoothing has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability terrain_geomorphometry has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability terrain_hydrology_advanced has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability terrain_sky_view has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability terrain_wetness_indices has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability trend_surface has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability triangulation_interpolation has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability variogram_analysis has no algorithm/tool provider and is not referenced by any workflow/template/fallback
- capability weights_sensitivity has no algorithm/tool provider and is not referenced by any workflow/template/fallback
### cycle_detected（1）

- capability:density_surface -> capability:grid_binning -> capability:density_surface (via capability)
### unreachable_tool（61）

- tool analyze_vegetation_index is not exposed by any algorithm and has no capability/deprecation link
- tool compile_workflow_semantics is not exposed by any algorithm and has no capability/deprecation link
- tool describe_artifact is not exposed by any algorithm and has no capability/deprecation link
- tool detect_vegetation_change is not exposed by any algorithm and has no capability/deprecation link
- tool fetch_sentinel is not exposed by any algorithm and has no capability/deprecation link
- tool find_artifacts_by_role is not exposed by any algorithm and has no capability/deprecation link
- tool get_child_districts is not exposed by any algorithm and has no capability/deprecation link
- tool get_district is not exposed by any algorithm and has no capability/deprecation link
- tool get_lineage is not exposed by any algorithm and has no capability/deprecation link
- tool get_local_child_districts is not exposed by any algorithm and has no capability/deprecation link
- tool get_sub_districts_polygons is not exposed by any algorithm and has no capability/deprecation link
- tool get_township_center is not exposed by any algorithm and has no capability/deprecation link
- tool get_upload_info is not exposed by any algorithm and has no capability/deprecation link
- tool gis_component_query is not exposed by any algorithm and has no capability/deprecation link
- tool gis_method_explain is not exposed by any algorithm and has no capability/deprecation link
- tool gis_method_qualify is not exposed by any algorithm and has no capability/deprecation link
- tool gis_method_rank is not exposed by any algorithm and has no capability/deprecation link
- tool gis_skill_detail is not exposed by any algorithm and has no capability/deprecation link
- tool gis_skill_replay_check is not exposed by any algorithm and has no capability/deprecation link
- tool gis_skill_search is not exposed by any algorithm and has no capability/deprecation link
- tool gis_task_classify is not exposed by any algorithm and has no capability/deprecation link
- tool gis_template_plan is not exposed by any algorithm and has no capability/deprecation link
- tool input_tips is not exposed by any algorithm and has no capability/deprecation link
- tool inventory_layers is not exposed by any algorithm and has no capability/deprecation link
- tool list_analysis_assets is not exposed by any algorithm and has no capability/deprecation link
- tool list_datasets is not exposed by any algorithm and has no capability/deprecation link
- tool list_uploaded_data is not exposed by any algorithm and has no capability/deprecation link
- tool materialize_dataset is not exposed by any algorithm and has no capability/deprecation link
- tool modelops_cancel_inference is not exposed by any algorithm and has no capability/deprecation link
- tool modelops_check_compatibility is not exposed by any algorithm and has no capability/deprecation link
- tool modelops_compare_results is not exposed by any algorithm and has no capability/deprecation link
- tool modelops_estimate_resources is not exposed by any algorithm and has no capability/deprecation link
- tool modelops_evaluate_model is not exposed by any algorithm and has no capability/deprecation link
- tool modelops_inspect_model is not exposed by any algorithm and has no capability/deprecation link
- tool modelops_inspect_provenance is not exposed by any algorithm and has no capability/deprecation link
- tool modelops_list_models is not exposed by any algorithm and has no capability/deprecation link
- tool modelops_model_history is not exposed by any algorithm and has no capability/deprecation link
- tool modelops_publish_layers is not exposed by any algorithm and has no capability/deprecation link
- tool modelops_record_metrics is not exposed by any algorithm and has no capability/deprecation link
- tool query_federated_data is not exposed by any algorithm and has no capability/deprecation link
- tool query_osm_boundary is not exposed by any algorithm and has no capability/deprecation link
- tool refresh_data_source is not exposed by any algorithm and has no capability/deprecation link
- tool run_spatial_simulation is not exposed by any algorithm and has no capability/deprecation link
- tool scenario_compare is not exposed by any algorithm and has no capability/deprecation link
- tool search_and_extract_poi is not exposed by any algorithm and has no capability/deprecation link
- tool search_datasets is not exposed by any algorithm and has no capability/deprecation link
- tool search_spatial_catalog is not exposed by any algorithm and has no capability/deprecation link
- tool update_layer_appearance is not exposed by any algorithm and has no capability/deprecation link
- tool validate_execution_plan is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_checkpoint is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_component_catalog is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_layer_remove is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_layer_upsert is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_layout_set is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_map_combine is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_map_intent is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_map_product is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_product_edit is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_rollback is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_state_get is not exposed by any algorithm and has no capability/deprecation link
- tool webgis_world_state is not exposed by any algorithm and has no capability/deprecation link
### artifact_no_consumer（5）

- artifact type change_set is produced but never accepted/bound by any node
- artifact type hotspot_result is produced but never accepted/bound by any node
- artifact type od_matrix is produced but never accepted/bound by any node
- artifact type proximity_zone is produced but never accepted/bound by any node
- artifact type service_area is produced but never accepted/bound by any node

