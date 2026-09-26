# Execution Catalog · Capability Chains

> capability → algorithm（priority 序）→ 已注册工具候选；
> 只列 native 实现链；planned/unavailable 如实缺席。

## accessibility（网络可达性）
- status: native；version: 1.0
- input: point_feature_set
- output: service_area, stats_table
- algorithm `network.accessibility`（priority=10；scientific=VALIDATED）→ tools: network_accessibility

## admin_aggregation（行政区聚合统计）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: admin_aggregate_table
- geometry: point
- algorithm `spatial.aggregate.admin`（priority=10；scientific=VALIDATED）→ tools: spatial_aggregate

## admin_boundary_query（行政区边界获取）
- status: native；version: 1.0
- output: admin_boundary_set, polygon_feature_set
- geometry: polygon
- algorithm `admin.boundary.local`（priority=10）→ tools: get_local_admin_boundary
- algorithm `admin.boundary_lookup`（priority=10；scientific=EXPERIMENTAL）→ tools: get_admin_division

## analytical_density（分析密度）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: admin_aggregate_table, density_surface
- geometry: point
- algorithm `density.analytical.mixed`（priority=30；scientific=VALIDATED）→ tools: kde_contours, heatmap_data, spatial_aggregate

## areal_interpolation（面插值（dasymetric））
- status: native；version: 1.0
- input: admin_aggregate_table, admin_boundary_set, polygon_feature_set
- output: polygon_feature_set
- geometry: polygon
- algorithm `interpolation.dasymetric`（priority=12；scientific=VALIDATED）→ tools: dasymetric_reallocation

## band_math（波段/栅格代数）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `raster.algebra`（priority=15；scientific=VALIDATED）→ tools: raster_calculator

## band_statistics（波段统计）
- status: native；version: 1.0
- input: raster_surface
- output: stats_table
- algorithm `remote.band_correlation`（priority=15；scientific=VALIDATED）→ tools: band_correlation_table

## bivariate_local_moran（双变量局部 Moran）
- status: native；version: 1.0
- input: admin_aggregate_table, grid_aggregate
- output: hotspot_result
- algorithm `stats.bivariate_local_moran`（priority=10；scientific=VALIDATED）→ tools: bivariate_local_moran

## bivariate_morans_i（双变量 Moran's I）
- status: native；version: 1.0
- input: admin_aggregate_table, grid_aggregate
- output: stats_table
- algorithm `stats.bivariate_moran`（priority=10；scientific=VALIDATED）→ tools: bivariate_moran

## block_kriging（块克里金）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: terrain_surface
- algorithm `interpolation.block_kriging`（priority=25；scientific=VALIDATED）→ tools: block_kriging_surface

## category_breakdown（类别构成统计）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- algorithm `stats.category.breakdown`（priority=10）→ tools: spatial_stats

## change_detection（时序要素变化检测）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: change_set
- algorithm `temporal.change`（priority=10；scientific=VALIDATED）→ tools: temporal_change

## closest_facility（最近设施）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: line_feature_set
- algorithm `network.closest_facility`（priority=10；scientific=VALIDATED）→ tools: network_closest_facility, nearest_facility

## cloud_qc_advisory（云 QC 咨询掩膜）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.cloud_qc`（priority=20；scientific=EXPERIMENTAL）→ tools: cloud_qc_basic

## cokriging（协同克里金）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: terrain_surface
- algorithm `interpolation.cokriging`（priority=24；scientific=VALIDATED）→ tools: cokriging_surface
- algorithm `interpolation.cokriging_lmc`（priority=29；scientific=VALIDATED）→ tools: cokriging_lmc_surface

## convex_hull（凸包）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set, polygon_feature_set
- output: polygon_feature_set
- algorithm `geometry.convex_hull`（priority=10；scientific=VALIDATED）→ tools: convex_hull

## cost_distance_analysis（累积成本面）
- status: native；version: 1.0
- input: raster_surface, terrain_surface
- output: raster_surface
- algorithm `terrain.cost_distance`（priority=46；scientific=VALIDATED）→ tools: cost_distance_analysis

## cross_k_function（双变量交叉 K 函数）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- geometry: point
- algorithm `point_pattern.cross_k`（priority=20；scientific=VALIDATED）→ tools: cross_k_analysis

## cross_pair_correlation（双变量成对相关函数 g12）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- geometry: point
- algorithm `point_pattern.cross_pcf`（priority=20；scientific=VALIDATED）→ tools: cross_pcf_analysis

## crs_transformation（坐标系转换）
- status: planned；version: 1.0
- algorithm `platform.crs_transformation`（priority=50）→ tools: reproject_coordinates, transform_coordinates

## data_source_pipeline（数据源管道）
- status: planned；version: 1.0
- algorithm `platform.data_source_pipeline`（priority=50）→ tools: aggregate_dataset, attribute_filter, connect_data_source, describe_dataset, inspect_data_source, manage_analysis_asset, plan_data_query, query_dataset, refresh_data_source, search_spatial_catalog

## dataset_ingest（数据集摄入）
- status: native；version: 1.0
- output: feature_collection
- algorithm `data.ingest.pipeline`（priority=10）→ tools: ingest_dataset

## dataset_profiling_quality（数据画像与质量）
- status: planned；version: 1.0
- algorithm `platform.dataset_profiling_quality`（priority=50）→ tools: audit_spatial_quality, profile_dataset, profile_dataset_semantics, repair_spatial_dataset, suggest_analysis_patterns

## density_surface（视觉密度面）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: density_surface
- geometry: point
- fallback: grid_binning
- algorithm `density.visual.heatmap`（priority=10；scientific=VALIDATED）→ tools: heatmap_data

## directional_distribution_analysis（方向分布分析）
- status: planned；version: 1.0
- algorithm `platform.directional_distribution_analysis`（priority=50）→ tools: standard_deviational_ellipse

## emerging_hotspot_analysis（时空热点演化（EHA））
- status: native；version: 1.0
- input: admin_aggregate_table, grid_aggregate, poi_feature_set, point_feature_set
- output: stats_table
- algorithm `temporal.emerging_hotspot`（priority=15；scientific=VALIDATED）→ tools: emerging_hotspot_analysis

## endmember_extraction（端元提取）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.endmember_vca`（priority=20；scientific=EXPERIMENTAL）→ tools: extract_endmembers_vca

## external_route_planning（外部路径规划）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: line_feature_set
- algorithm `network.route_external_api`（priority=40；scientific=EXPERIMENTAL）→ tools: plan_route

## federated_dataset_query（联邦数据集查询）
- status: native；version: 1.0
- output: stats_table
- algorithm `data.federated.chain`（priority=10）→ tools: query_federated_chain

## general_g（Getis-Ord General G）
- status: native；version: 1.0
- input: admin_aggregate_table, poi_feature_set, point_feature_set
- output: stats_table
- algorithm `stats.general_g`（priority=10；scientific=VALIDATED）→ tools: general_g

## geocoding（地理编码）
- status: planned；version: 1.0
- algorithm `platform.geocoding`（priority=50）→ tools: geocode, geocode_cn, batch_geocode_cn, reverse_geocode, reverse_geocode_cn

## geographical_detector（地理探测器）
- status: native；version: 1.0
- input: admin_aggregate_table, grid_aggregate, poi_feature_set, point_feature_set
- output: stats_table
- algorithm `stats.geodetector`（priority=10；scientific=VALIDATED）→ tools: geodetector
- algorithm `stats.geodetector_ecological`（priority=10；scientific=VALIDATED）→ tools: geodetector_ecological
- algorithm `stats.geodetector_risk`（priority=10；scientific=VALIDATED）→ tools: geodetector_risk

## geometry_buffer（几何缓冲）
- status: native；version: 1.0
- input: line_feature_set, poi_feature_set, point_feature_set, polygon_feature_set
- output: proximity_zone
- algorithm `geometry.buffer`（priority=20；scientific=VALIDATED）→ tools: buffer_analysis

## geometry_centroid（几何中心统计）
- status: native；version: 1.0
- input: line_feature_set, poi_feature_set, point_feature_set, polygon_feature_set
- output: stats_table
- algorithm `geometry.center_statistics`（priority=30；scientific=EXPERIMENTAL）→ tools: spatial_stats, central_feature

## geometry_clip（几何裁剪）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set, polygon_feature_set
- output: polygon_feature_set
- algorithm `geometry.clip`（priority=10）→ tools: clip_layer

## geometry_dissolve（融合/溶解）
- status: native；version: 1.0
- input: admin_boundary_set, polygon_feature_set
- output: polygon_feature_set
- algorithm `geometry.dissolve`（priority=10）→ tools: dissolve_layer

## geometry_overlay（几何叠加）
- status: native；version: 1.0
- input: line_feature_set, poi_feature_set, point_feature_set, polygon_feature_set
- output: line_feature_set, point_feature_set, polygon_feature_set
- algorithm `geometry.overlay`（priority=10；scientific=VALIDATED）→ tools: overlay_analysis

## geostatistical_simulation（地统计模拟）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: terrain_surface
- algorithm `interpolation.sgs`（priority=28；scientific=VALIDATED）→ tools: sgs_simulation

## getis_ord_gi_star（Getis-Ord Gi*）
- status: native；version: 1.0
- input: grid_aggregate, poi_feature_set, point_feature_set
- output: hotspot_result
- algorithm `stats.h3_hotspot`（priority=15；scientific=VALIDATED）→ tools: hotspot_analysis

## global_gearys_c（全局 Geary 指数）
- status: native；version: 1.0
- input: admin_aggregate_table, poi_feature_set, point_feature_set
- output: stats_table
- algorithm `stats.gearys_c`（priority=10；scientific=VALIDATED）→ tools: geary_c

## global_morans_i（全局莫兰指数）
- status: native；version: 1.0
- input: admin_aggregate_table, poi_feature_set, point_feature_set
- output: stats_table
- algorithm `stats.morans_i`（priority=10；scientific=VALIDATED）→ tools: moran_i

## gravity_accessibility（引力可达性）
- status: native；version: 1.0
- input: point_feature_set
- output: stats_table
- algorithm `network.gravity_access`（priority=20；scientific=VALIDATED）→ tools: network_gravity_access

## grid_binning（格网聚合）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: grid_aggregate
- geometry: point
- fallback: density_surface
- algorithm `spatial.grid.fishnet`（priority=20；scientific=VALIDATED）→ tools: fishnet_grid
- algorithm `spatial.grid.h3`（priority=10；scientific=VALIDATED）→ tools: h3_binning

## gwr（地理加权回归 (GWR)）
- status: native；version: 1.0
- input: admin_aggregate_table, grid_aggregate, poi_feature_set, point_feature_set
- output: stats_table
- algorithm `spatial.gwr`（priority=30；scientific=VALIDATED）→ tools: gwr_regression
- algorithm `spatial.mgwr`（priority=40；scientific=VALIDATED）→ tools: mgwr_regression

## habitat_suitability（生境适宜度）
- status: native；version: 1.0
- input: admin_aggregate_table, grid_aggregate, point_feature_set, polygon_feature_set
- output: feature_collection
- algorithm `ecology.habitat_suitability`（priority=20；scientific=VALIDATED）→ tools: habitat_suitability_analysis

## hotspot（热点显著性分析）
- status: native；version: 1.0
- input: grid_aggregate, poi_feature_set, point_feature_set
- output: hotspot_result
- geometry: point
- algorithm `spatial.hotspot.local`（priority=10；scientific=VALIDATED）→ tools: hotspot_analysis

## ica_transform（ICA 独立成分分析）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.ica`（priority=15；scientific=VALIDATED）→ tools: ica_transform

## image_segmentation（图像分割）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.segmentation`（priority=15；scientific=VALIDATED）→ tools: segment_image

## indicator_kriging（指示克里金）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: terrain_surface
- algorithm `interpolation.indicator_kriging`（priority=23；scientific=VALIDATED）→ tools: indicator_kriging_surface

## interpolation_model_selection（插值模型选择）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- algorithm `interpolation.model_compare`（priority=18；scientific=VALIDATED）→ tools: interpolation_model_compare

## join_count_statistics（Join Count 统计）
- status: native；version: 1.0
- input: admin_aggregate_table
- output: stats_table
- algorithm `stats.bivariate_join_count`（priority=10；scientific=VALIDATED）→ tools: bivariate_join_count
- algorithm `stats.join_count`（priority=10；scientific=VALIDATED）→ tools: join_count

## kde_density（核密度估计）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: density_surface
- geometry: point
- algorithm `spatial.kde.contours`（priority=10；scientific=VALIDATED）→ tools: kde_contours
- algorithm `spatial.kde.surface`（priority=20；scientific=VALIDATED）→ tools: kde_surface

## landscape_metrics（景观格局指标）
- status: native；version: 1.0
- input: raster_surface, terrain_surface
- output: stats_table
- algorithm `ecology.landscape_metrics`（priority=20；scientific=VALIDATED）→ tools: landscape_metrics_analysis

## layer_display_control（图层显示控制）
- status: planned；version: 1.0
- algorithm `platform.layer_display_control`（priority=50）→ tools: alias_layer, apply_layer_filter, display_layer, finalize_display, remove_layer, reorder_layer, set_layer_status, switch_base_layer, update_layer_appearance

## least_cost_path_analysis（最小成本路径）
- status: native；version: 1.0
- input: raster_surface
- output: line_feature_set
- algorithm `terrain.least_cost_path`（priority=47；scientific=VALIDATED）→ tools: least_cost_path_analysis

## local_data_query（本地数据目录与查询）
- status: planned；version: 1.0
- algorithm `platform.local_data_query`（priority=50）→ tools: get_local_osm_catalog, get_local_stats_catalog, query_local_osm, query_local_yearbook, query_osm_buildings, query_osm_roads

## local_gearys_c（局部 Geary's C）
- status: native；version: 1.0
- input: admin_aggregate_table, grid_aggregate
- output: hotspot_result
- algorithm `stats.local_geary`（priority=10；scientific=VALIDATED）→ tools: local_geary

## local_join_count（局部 Join Count）
- status: native；version: 1.0
- input: admin_aggregate_table
- output: hotspot_result
- algorithm `stats.local_join_count`（priority=10；scientific=VALIDATED）→ tools: local_join_count

## local_morans_i（局部莫兰/LISA）
- status: native；version: 1.0
- input: admin_aggregate_table, poi_feature_set, point_feature_set
- output: hotspot_result
- algorithm `stats.h3_lisa`（priority=10；scientific=VALIDATED）→ tools: h3_lisa
- algorithm `stats.local_moran`（priority=10；scientific=VALIDATED）→ tools: local_moran

## location_allocation（区位配置）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: point_feature_set, stats_table
- algorithm `network.location_allocation`（priority=30；scientific=VALIDATED）→ tools: location_allocation
- algorithm `network.mclp_exact`（priority=32；scientific=VALIDATED）→ tools: location_allocation
- algorithm `network.pcenter_exact`（priority=31；scientific=VALIDATED）→ tools: location_allocation
- algorithm `network.pmedian_exact`（priority=31；scientific=VALIDATED）→ tools: location_allocation

## mantel_test（Mantel 时空检验）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- geometry: point
- algorithm `point_pattern.mantel`（priority=20；scientific=VALIDATED）→ tools: mantel_test_analysis

## map_annotation_measurement（地图标注与量测）
- status: planned；version: 1.0
- algorithm `platform.map_annotation_measurement`（priority=50）→ tools: add_marker, clear_annotations, measure_area, measure_distance, query_map_features

## map_export_publishing（地图导出发布）
- status: planned；version: 1.0
- algorithm `platform.map_export_publishing`（priority=50）→ tools: export_batch_maps, export_thematic_map, webgis_cartography_status, webgis_compile_maplibre, webgis_project_init, webgis_runtime_validate, webgis_validate

## map_viewport_control（地图视口控制）
- status: planned；version: 1.0
- algorithm `platform.map_viewport_control`（priority=50）→ tools: fly_to_location, set_map_view, reset_map_view, zoom_to_bbox, zoom_to_layer, webgis_view_set

## mcda_evaluation（多准则决策评价）
- status: native；version: 1.0
- input: admin_aggregate_table, poi_feature_set, point_feature_set, polygon_feature_set
- output: stats_table
- algorithm `decision.mcda.wsm`（priority=40；scientific=VALIDATED）→ tools: spatial_decision_v3

## meta_tool_surface（元工具面）
- status: planned；version: 1.0
- algorithm `platform.meta_tool_surface`（priority=50）→ tools: create_new_skill, deep_explore, list_available_tools, refresh_skill_surface, spawn_subagent, web_search

## mnf_transform（MNF 变换）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.mnf`（priority=15；scientific=VALIDATED）→ tools: mnf_transform

## model_change_detection（模型变化检测）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `model.inference.change_detection`（priority=30；scientific=VALIDATED）→ tools: modelops_run_inference, modelops_run_promptable

## model_embedding（模型特征嵌入）
- status: native；version: 1.0
- input: raster_surface
- output: feature_collection
- algorithm `model.inference.embedding`（priority=30；scientific=VALIDATED）→ tools: modelops_run_inference, modelops_run_promptable

## model_image_segmentation（模型影像分割）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `model.inference.semantic_segmentation`（priority=30；scientific=VALIDATED）→ tools: modelops_run_inference, modelops_run_promptable

## model_instance_segmentation（模型实例分割）
- status: native；version: 1.0
- input: raster_surface
- output: polygon_feature_set, raster_surface
- algorithm `model.inference.instance_segmentation`（priority=30；scientific=VALIDATED）→ tools: modelops_run_inference, modelops_run_promptable

## model_object_detection（模型目标检测）
- status: native；version: 1.0
- input: raster_surface
- output: polygon_feature_set
- algorithm `model.inference.object_detection`（priority=30；scientific=VALIDATED）→ tools: modelops_run_inference, modelops_run_promptable

## model_promptable_segmentation（模型可提示分割）
- status: native；version: 1.0
- input: raster_surface
- output: polygon_feature_set, raster_surface
- algorithm `model.inference.promptable_segmentation`（priority=30；scientific=VALIDATED）→ tools: geoai_run_promptable, modelops_run_promptable, geoai_prompt_refine

## model_super_resolution（模型超分辨率）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `model.inference.super_resolution`（priority=30；scientific=VALIDATED）→ tools: modelops_run_inference, modelops_run_promptable

## model_temporal_classification（模型时序分类）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `model.inference.temporal_classification`（priority=30；scientific=VALIDATED）→ tools: modelops_run_inference, modelops_run_promptable

## model_temporal_forecast（模型时序预测）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `model.inference.temporal_forecast`（priority=30；scientific=VALIDATED）→ tools: modelops_run_inference, modelops_run_promptable

## multi_ring_buffer（多环缓冲）
- status: native；version: 1.0
- input: line_feature_set, poi_feature_set, point_feature_set, polygon_feature_set
- output: proximity_zone
- algorithm `geometry.multi_ring_buffer`（priority=10；scientific=VALIDATED）→ tools: multi_ring_buffer

## ndvi（NDVI 植被指数）
- status: native；version: 1.0
- input: raster_surface, terrain_surface
- output: raster_surface
- algorithm `remote.ndvi`（priority=10；scientific=VALIDATED）→ tools: compute_ndvi, compute_vegetation_index

## nearest_neighbor_functions（最近邻距离函数）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- geometry: point
- algorithm `point_pattern.g_f_j`（priority=20；scientific=VALIDATED）→ tools: g_f_j_analysis

## network_centrality（网络中心性）
- status: native；version: 1.0
- output: stats_table
- algorithm `network.centrality`（priority=20；scientific=VALIDATED）→ tools: network_centrality
- algorithm `network.eigenvector_centrality`（priority=21；scientific=VALIDATED）→ tools: network_centrality

## od_flow_mapping（OD 流向图）
- status: native；version: 1.0
- input: line_feature_set, od_matrix, od_table
- output: line_feature_set
- algorithm `flow.od_arc_build`（priority=10；scientific=VALIDATED）→ tools: od_flow_edges

## od_matrix（OD 成本矩阵）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: od_matrix
- algorithm `network.od_matrix`（priority=10；scientific=VALIDATED）→ tools: network_od_matrix, distance_matrix_cn

## pair_correlation_function（成对相关函数）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- geometry: point
- algorithm `point_pattern.pcf`（priority=20；scientific=VALIDATED）→ tools: pcf_analysis

## plan_workflow_orchestration（计划与工作流编排）
- status: planned；version: 1.0
- algorithm `platform.plan_workflow_orchestration`（priority=50）→ tools: cancel_execution_run, execute_execution_plan, execute_plan, get_execution_run, get_plan_status, propose_plan, rerun_workflow, save_plan_as_workflow, validate_execution_plan

## poi_query（POI 要素获取）
- status: native；version: 1.0
- output: poi_feature_set
- geometry: point
- algorithm `poi.area_search`（priority=20；scientific=EXPERIMENTAL）→ tools: search_poi_around, search_poi_polygon
- algorithm `poi.query.local`（priority=10）→ tools: query_local_poi, search_poi, query_osm_poi

## point_pattern_analysis（点格局分析）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: hotspot_result, stats_table
- geometry: point
- algorithm `point_pattern.dbscan`（priority=20；scientific=VALIDATED）→ tools: spatial_cluster
- algorithm `point_pattern.nni`（priority=20；scientific=VALIDATED）→ tools: nearest_neighbor
- algorithm `point_pattern.quadrat_test`（priority=10；scientific=VALIDATED）→ tools: quadrat_analysis
- algorithm `point_pattern.ripley_k`（priority=10；scientific=VALIDATED）→ tools: ripley_k_analysis
- algorithm `point_pattern.ripley_k_env`（priority=20；scientific=VALIDATED）→ tools: ripley_k_envelope_analysis

## point_profile（数据画像）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: point_feature_set
- geometry: point
- algorithm `profile.spatial.stats`（priority=10；scientific=VALIDATED）→ tools: spatial_stats, webgis_source_profile

## proximity_buffer（邻近缓冲）
- status: native；version: 1.0
- input: line_feature_set, poi_feature_set, point_feature_set, polygon_feature_set
- output: proximity_zone
- algorithm `spatial.buffer.proximity`（priority=10；scientific=VALIDATED）→ tools: buffer_analysis

## radiometric_normalization（辐射归一化）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.robust_normalize`（priority=15；scientific=VALIDATED）→ tools: robust_normalize

## raster_change_detection（双时相栅格变化检测）
- status: native；version: 1.0
- input: raster_surface
- output: change_set, raster_surface
- algorithm `remote.change.raster`（priority=10；scientific=VALIDATED）→ tools: detect_raster_change
- algorithm `remote.cva`（priority=15；scientific=VALIDATED）→ tools: detect_change_cva
- algorithm `remote.mad_change`（priority=15；scientific=VALIDATED）→ tools: mad_change
- algorithm `remote.ratio_change`（priority=15；scientific=VALIDATED）→ tools: detect_ratio_change

## raster_cog_conversion（COG 栅格转换）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `raster.cog.convert`（priority=10）→ tools: convert_raster_to_cog

## raster_dimensionality_reduction（波段降维（PCA））
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.pca`（priority=15；scientific=VALIDATED）→ tools: raster_pca

## raster_reclassify（栅格重分类）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `raster.reclassify.rule`（priority=10；scientific=VALIDATED）→ tools: raster_reclassify

## raster_resample（栅格重采样）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `raster.resample.grid`（priority=10；scientific=VALIDATED）→ tools: raster_resample

## raster_source（栅格数据源）
- status: native；version: 1.0
- output: raster_surface, terrain_surface
- geometry: raster
- algorithm `raster.source.dem`（priority=10；scientific=EXPERIMENTAL）→ tools: fetch_dem

## rate_aggregation（率/密度聚合）
- status: native；version: 1.0
- input: admin_boundary_set, poi_feature_set, point_feature_set, polygon_feature_set
- output: admin_aggregate_table
- algorithm `spatial.aggregate.rates`（priority=15；scientific=EXPERIMENTAL）→ tools: spatial_aggregate

## rate_smoothing（经验贝叶斯率平滑）
- status: native；version: 1.0
- input: admin_aggregate_table, admin_boundary_set, polygon_feature_set
- output: admin_aggregate_table
- algorithm `stats.rate_smoothing`（priority=15；scientific=VALIDATED）→ tools: rate_smoothing

## regression_kriging（回归克里金）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: terrain_surface
- algorithm `interpolation.regression_kriging`（priority=22；scientific=VALIDATED）→ tools: regression_kriging

## report_charting（报告与图表）
- status: planned；version: 1.0
- algorithm `platform.report_charting`（priority=50）→ tools: generate_analysis_report, generate_chart, generate_monitoring_report

## route_optimization（路线优化）
- status: native；version: 1.0
- input: point_feature_set
- output: line_feature_set
- algorithm `network.optimize_route`（priority=30；scientific=VALIDATED）→ tools: optimize_route
- algorithm `network.route_optimization`（priority=10；scientific=VALIDATED）→ tools: optimize_route

## rs_cube_alignment（光学/SAR 获取对齐）
- status: native；version: 1.0
- input: rs_cube_descriptor
- output: rs_cube_descriptor, stats_table
- geometry: raster
- algorithm `remote.cube.align`（priority=20；scientific=VALIDATED）→ tools: rs_cube_align

## rs_cube_describe（时序立方体描述）
- status: native；version: 1.0
- input: rs_cube_descriptor
- output: rs_cube_descriptor
- geometry: raster
- algorithm `remote.cube.describe`（priority=15；scientific=VALIDATED）→ tools: rs_cube_describe

## rs_joint_fusion（SAR×光学联合特征融合）
- status: native；version: 1.0
- input: raster_surface, rs_cube_descriptor
- output: raster_surface, stats_table
- geometry: raster
- algorithm `remote.cube.fusion`（priority=20；scientific=VALIDATED）→ tools: rs_joint_fusion_stack

## rs_sample_split（样本挂接与防泄漏分割）
- status: native；version: 1.0
- input: polygon_feature_set, raster_surface
- output: stats_table
- algorithm `remote.cube.samples`（priority=20；scientific=VALIDATED）→ tools: rs_cube_sample_split

## rs_temporal_feature_pack（时序特征包）
- status: native；version: 1.0
- input: raster_surface, rs_cube_descriptor
- output: raster_surface, stats_table
- geometry: raster
- algorithm `remote.cube.features`（priority=20；scientific=VALIDATED）→ tools: rs_temporal_feature_pack

## rx_anomaly_detection（RX 异常检测）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.rx_anomaly`（priority=15；scientific=VALIDATED）→ tools: rx_anomaly

## sar_analysis（SAR 时序/极化分析）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `sar.log_ratio_change`（priority=20；scientific=VALIDATED）→ tools: detect_ratio_change
- algorithm `sar.temporal_composite`（priority=15；scientific=VALIDATED）→ tools: sar_temporal_composite
- algorithm `sar.temporal_stats`（priority=15；scientific=VALIDATED）→ tools: sar_temporal_stats
- algorithm `sar.vh_ratio`（priority=15；scientific=VALIDATED）→ tools: sar_vh_ratio

## sar_coherence（SAR 相干性估计）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `sar.coherence`（priority=20；scientific=EXPERIMENTAL）→ tools: sar_coherence_estimate

## sar_radiometric_calibration（SAR 辐射定标）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `sar.log_scaling`（priority=20；scientific=VALIDATED）→ tools: sar_log_scale
- algorithm `sar.radiometric_calibration`（priority=15；scientific=VALIDATED）→ tools: sar_calibrate
- algorithm `sar.thermal_noise_removal`（priority=20；scientific=VALIDATED）→ tools: sar_remove_thermal_noise

## sar_speckle_filtering（SAR 斑点滤波）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `sar.enl_map`（priority=20；scientific=VALIDATED）→ tools: sar_enl_map
- algorithm `sar.multitemporal_speckle`（priority=15；scientific=VALIDATED）→ tools: sar_multitemporal_speckle
- algorithm `sar.speckle_filter`（priority=15；scientific=VALIDATED）→ tools: sar_speckle_filter

## sar_terrain_geometry_correction（SAR 地形几何/辐射校正）
- status: native；version: 1.0
- input: raster_surface, terrain_surface
- output: raster_surface
- algorithm `sar.layover_shadow`（priority=15；scientific=VALIDATED）→ tools: sar_layover_shadow_mask
- algorithm `sar.rtc`（priority=15；scientific=VALIDATED）→ tools: sar_radiometric_terrain_correction

## sar_texture（GLCM 纹理特征）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `sar.glcm_texture`（priority=15；scientific=VALIDATED）→ tools: sar_glcm_texture

## scenario_simulation（情景推演）
- status: planned；version: 1.0
- algorithm `platform.scenario_simulation`（priority=50）→ tools: spatial_decision_v2, spatial_reasoning, what_if_simulate

## service_area（网络服务区）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: service_area
- algorithm `network.isochrone`（priority=10；scientific=EXPERIMENTAL）→ tools: isochrone_analysis
- algorithm `network.isochrone.local`（priority=30；scientific=VALIDATED）→ tools: isochrone_network
- algorithm `network.service_area.multi`（priority=25；scientific=VALIDATED）→ tools: network_service_area
- algorithm `network.service_area.simple`（priority=20；scientific=EXPERIMENTAL）→ tools: service_area_simple

## shortest_path（最短路径）
- status: native；version: 1.0
- output: line_feature_set
- algorithm `network.shortest_path`（priority=10；scientific=VALIDATED）→ tools: network_shortest_path

## space_time_interaction（时空交互检验）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- geometry: point
- algorithm `spatiotemporal.knox`（priority=20；scientific=VALIDATED）→ tools: knox_analysis

## space_time_k_function（时空 K 函数）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- geometry: point
- algorithm `point_pattern.space_time_k`（priority=20；scientific=VALIDATED）→ tools: space_time_k_analysis

## spatial_interaction（空间相互作用）
- status: native；version: 1.0
- input: point_feature_set
- output: stats_table
- algorithm `network.huff_interaction`（priority=20；scientific=VALIDATED）→ tools: network_huff_interaction

## spatial_interpolation（空间插值）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: terrain_surface
- algorithm `interpolation.external_drift_kriging`（priority=27；scientific=VALIDATED）→ tools: kriging_interpolation
- algorithm `interpolation.idw`（priority=10；scientific=VALIDATED）→ tools: idw_interpolation
- algorithm `interpolation.kriging`（priority=20；scientific=PRODUCTION）→ tools: kriging_interpolation
- algorithm `interpolation.nearest_neighbor`（priority=11；scientific=VALIDATED）→ tools: nearest_neighbor_surface
- algorithm `interpolation.rbf`（priority=15；scientific=VALIDATED）→ tools: rbf_interpolation
- algorithm `interpolation.simple_kriging`（priority=26；scientific=VALIDATED）→ tools: kriging_interpolation
- algorithm `interpolation.universal_kriging`（priority=21；scientific=VALIDATED）→ tools: kriging_interpolation

## spatial_join（空间连接）
- status: native；version: 1.0
- input: poi_feature_set, polygon_feature_set
- output: polygon_feature_set
- algorithm `geometry.spatial_join`（priority=20）→ tools: spatial_join

## spatial_regression（空间回归）
- status: native；version: 1.0
- input: admin_aggregate_table, grid_aggregate, poi_feature_set, point_feature_set
- output: stats_table
- algorithm `spatial.ols_regression`（priority=10；scientific=VALIDATED）→ tools: ols_regression
- algorithm `spatial.sar_ml`（priority=20；scientific=VALIDATED）→ tools: sar_ml_regression
- algorithm `spatial.sem_ml`（priority=20；scientific=VALIDATED）→ tools: sem_ml_regression
- algorithm `spatial.slx`（priority=20；scientific=VALIDATED）→ tools: slx_regression

## spatial_sampling（空间抽样）
- status: native；version: 1.0
- input: admin_aggregate_table, admin_boundary_set, polygon_feature_set
- output: point_feature_set
- geometry: polygon
- algorithm `sampling.random_points`（priority=20；scientific=VALIDATED）→ tools: sample_random_points
- algorithm `sampling.stratified_points`（priority=20；scientific=VALIDATED）→ tools: sample_stratified_points
- algorithm `sampling.systematic_grid`（priority=20；scientific=VALIDATED）→ tools: sample_systematic_grid

## spatial_weights_diagnostics（空间权重诊断）
- status: native；version: 1.0
- input: admin_aggregate_table, grid_aggregate, poi_feature_set, point_feature_set
- output: stats_table
- algorithm `stats.weights_diagnostics`（priority=10；scientific=VALIDATED）→ tools: weights_diagnostics

## spatiotemporal_clustering（时空聚类）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: hotspot_result
- algorithm `stats.st_dbscan`（priority=20；scientific=VALIDATED）→ tools: st_dbscan, spatial_cluster
- algorithm `temporal.hotspot`（priority=15；scientific=VALIDATED）→ tools: spatiotemporal_hotspot

## spatiotemporal_interpolation（时空插值）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: terrain_surface
- algorithm `interpolation.st_kriging`（priority=30；scientific=VALIDATED）→ tools: st_kriging_surface

## spectral_index（类型化光谱指数）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.spectral_index`（priority=15；scientific=VALIDATED）→ tools: compute_spectral_index

## spectral_target_detection（光谱目标检测）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.matched_filter`（priority=15；scientific=VALIDATED）→ tools: matched_filter
- algorithm `remote.sam`（priority=15；scientific=VALIDATED）→ tools: spectral_angle_mapper
- algorithm `remote.sid`（priority=15；scientific=VALIDATED）→ tools: spectral_information_divergence

## spectral_unmixing（线性光谱解混）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.linear_unmixing`（priority=15；scientific=VALIDATED）→ tools: linear_unmixing

## tasseled_cap_transformation（Tasseled Cap 冠层变换）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.tasseled_cap`（priority=15；scientific=VALIDATED）→ tools: tasseled_cap

## temporal_aggregate（时间聚合）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- algorithm `temporal.aggregate`（priority=10；scientific=VALIDATED）→ tools: temporal_aggregate

## temporal_change_point（时序均值变点）
- status: native；version: 1.0
- input: stats_table
- output: stats_table
- algorithm `temporal.changepoint`（priority=15；scientific=VALIDATED）→ tools: temporal_changepoint

## temporal_composite（多时相合成）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.medoid_composite`（priority=15；scientific=VALIDATED）→ tools: medoid_composite

## temporal_feature_extraction（时序特征提取）
- status: native；version: 1.0
- input: raster_surface
- output: raster_surface
- algorithm `remote.temporal_features`（priority=15；scientific=VALIDATED）→ tools: temporal_features

## temporal_filtering（时间筛选）
- status: planned；version: 1.0
- algorithm `platform.temporal_filtering`（priority=50）→ tools: temporal_filter

## temporal_profile（时间画像）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- algorithm `temporal.profile`（priority=10；scientific=VALIDATED）→ tools: temporal_profile

## temporal_smoothing（时序平滑与缺口填补）
- status: native；version: 1.0
- input: raster_surface, stats_table
- output: stats_table
- algorithm `temporal.smooth_gapfill`（priority=15；scientific=VALIDATED）→ tools: ts_smooth_gapfill

## temporal_trend（时序趋势）
- status: native；version: 1.0
- input: poi_feature_set, raster_surface, stats_table
- output: raster_surface, stats_table
- algorithm `temporal.anomaly`（priority=33；scientific=VALIDATED）→ tools: temporal_anomaly
- algorithm `temporal.cube_stats`（priority=31；scientific=VALIDATED）→ tools: temporal_cube_stats
- algorithm `temporal.phenology`（priority=32；scientific=VALIDATED）→ tools: phenology_features
- algorithm `temporal.raster_ts`（priority=30；scientific=VALIDATED）→ tools: temporal_raster
- algorithm `temporal.seasonal_decompose`（priority=15；scientific=VALIDATED）→ tools: temporal_seasonal_decompose
- algorithm `temporal.trend`（priority=10；scientific=VALIDATED）→ tools: temporal_trend

## terrain_aspect（坡向分析）
- status: native；version: 1.0
- input: terrain_surface
- output: terrain_surface
- algorithm `terrain.aspect`（priority=30；scientific=VALIDATED）→ tools: compute_terrain

## terrain_contours（等值线提取）
- status: native；version: 1.0
- input: terrain_surface
- output: line_feature_set
- algorithm `terrain.contours`（priority=47；scientific=VALIDATED）→ tools: extract_contours

## terrain_derivatives（地形衍生指标）
- status: native；version: 1.0
- input: terrain_surface
- output: raster_surface
- algorithm `terrain.curvature`（priority=43；scientific=VALIDATED）→ tools: terrain_derivatives
- algorithm `terrain.roughness`（priority=42；scientific=VALIDATED）→ tools: terrain_derivatives
- algorithm `terrain.solar_radiation`（priority=50；scientific=VALIDATED）→ tools: hydrology_v4_analysis
- algorithm `terrain.tpi`（priority=40；scientific=VALIDATED）→ tools: terrain_derivatives
- algorithm `terrain.tri`（priority=41；scientific=VALIDATED）→ tools: terrain_derivatives

## terrain_geomorphometry（地貌形态分类）
- status: native；version: 1.0
- input: terrain_surface
- output: raster_surface, stats_table
- algorithm `terrain.geomorphons`（priority=58；scientific=VALIDATED）→ tools: geomorphon_analysis
- algorithm `terrain.hillshade_multi`（priority=60；scientific=VALIDATED）→ tools: multiazimuth_hillshade
- algorithm `terrain.hypsometry`（priority=50；scientific=VALIDATED）→ tools: hydrology_v4_analysis
- algorithm `terrain.landform`（priority=59；scientific=VALIDATED）→ tools: landform_classify
- algorithm `terrain.openness`（priority=57；scientific=VALIDATED）→ tools: terrain_openness_analysis

## terrain_hillshade（山体阴影）
- status: native；version: 1.0
- input: terrain_surface
- output: terrain_surface
- algorithm `terrain.hillshade`（priority=20；scientific=VALIDATED）→ tools: compute_terrain

## terrain_hydrology（D8 水文分析）
- status: native；version: 1.0
- input: terrain_surface
- output: raster_surface, stats_table
- algorithm `terrain.breach`（priority=50；scientific=VALIDATED）→ tools: hydrology_v4_analysis
- algorithm `terrain.flow`（priority=45；scientific=VALIDATED）→ tools: flow_analysis
- algorithm `terrain.flow_topology_validate`（priority=50；scientific=VALIDATED）→ tools: hydrology_v4_analysis
- algorithm `terrain.hand`（priority=50；scientific=VALIDATED）→ tools: hydrology_v4_analysis
- algorithm `terrain.pfafstetter`（priority=50；scientific=VALIDATED）→ tools: hydrology_v4_analysis
- algorithm `terrain.pfafstetter_multilevel`（priority=50；scientific=VALIDATED）→ tools: hydrology_v4_analysis
- algorithm `terrain.shreve`（priority=50；scientific=VALIDATED）→ tools: hydrology_v4_analysis
- algorithm `terrain.watershed`（priority=46；scientific=VALIDATED）→ tools: watershed_delineation

## terrain_hydrology_advanced（高级地形水文）
- status: native；version: 1.0
- input: terrain_surface
- output: raster_surface
- algorithm `terrain.dinf_flow`（priority=49；scientific=VALIDATED）→ tools: dinf_flow_analysis
- algorithm `terrain.flow_length`（priority=50；scientific=VALIDATED）→ tools: flow_length_analysis
- algorithm `terrain.morphometry`（priority=53；scientific=VALIDATED）→ tools: watershed_morphometry_analysis
- algorithm `terrain.sink_fill`（priority=48；scientific=VALIDATED）→ tools: depression_fill
- algorithm `terrain.strahler`（priority=52；scientific=VALIDATED）→ tools: stream_network
- algorithm `terrain.streams`（priority=51；scientific=VALIDATED）→ tools: stream_network

## terrain_sky_view（地平线与天空可视因子）
- status: native；version: 1.0
- input: terrain_surface
- output: raster_surface
- algorithm `terrain.horizon_angle`（priority=61；scientific=VALIDATED）→ tools: horizon_angle_analysis
- algorithm `terrain.sky_view_factor`（priority=62；scientific=VALIDATED）→ tools: sky_view_factor_analysis

## terrain_slope（坡度分析）
- status: native；version: 1.0
- input: terrain_surface
- output: terrain_surface
- algorithm `terrain.slope`（priority=10；scientific=VALIDATED）→ tools: compute_terrain

## terrain_viewshed（视域分析）
- status: native；version: 1.0
- input: terrain_surface
- output: raster_surface
- algorithm `terrain.viewshed`（priority=44；scientific=VALIDATED）→ tools: viewshed_analysis

## terrain_wetness_indices（湿润与侵蚀指数）
- status: native；version: 1.0
- input: terrain_surface
- output: raster_surface
- algorithm `terrain.ls_factor`（priority=56；scientific=VALIDATED）→ tools: ls_factor_analysis
- algorithm `terrain.spi`（priority=55；scientific=VALIDATED）→ tools: topographic_index
- algorithm `terrain.twi`（priority=54；scientific=VALIDATED）→ tools: topographic_index

## thematic_cartography（专题制图与样式）
- status: planned；version: 1.0
- algorithm `platform.thematic_cartography`（priority=50）→ tools: apply_layer_style, apply_template, combine_map_theme, control_floating_chart, create_3d_extrusion_map, create_thematic_map, list_templates, webgis_component_update, webgis_layout_set, webgis_map_combine, webgis_layer_upsert, webgis_layer_remove, webgis_map_intent, webgis_map_product

## traffic_status（实时路况）
- status: native；version: 1.0
- output: stats_table
- algorithm `network.traffic_status_external`（priority=40；scientific=EXPERIMENTAL）→ tools: get_traffic_status

## transit_routing（公交路径规划）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: line_feature_set
- algorithm `network.transit_route_external`（priority=40；scientific=EXPERIMENTAL）→ tools: search_transit_route

## trend_surface（趋势面分析）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: terrain_surface
- algorithm `interpolation.trend_surface`（priority=17；scientific=VALIDATED）→ tools: trend_surface

## triangulation_interpolation（三角网插值）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: terrain_surface
- algorithm `interpolation.natural_neighbor`（priority=19；scientific=VALIDATED）→ tools: natural_neighbor_surface
- algorithm `interpolation.tin`（priority=16；scientific=VALIDATED）→ tools: tin_interpolation

## variogram_analysis（变异函数分析）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: stats_table
- algorithm `interpolation.directional_variogram`（priority=50；scientific=VALIDATED）→ tools: directional_variogram_analysis
- algorithm `interpolation.variogram_selection`（priority=50；scientific=VALIDATED）→ tools: variogram_model_selection

## voronoi_tessellation（Voronoi 剖分）
- status: native；version: 1.0
- input: poi_feature_set, point_feature_set
- output: polygon_feature_set
- geometry: point
- algorithm `geometry.voronoi`（priority=10；scientific=VALIDATED）→ tools: voronoi_polygons

## weights_sensitivity（权重敏感性）
- status: native；version: 1.0
- input: admin_aggregate_table, grid_aggregate, poi_feature_set, point_feature_set
- output: stats_table
- algorithm `stats.weights_sensitivity`（priority=10；scientific=VALIDATED）→ tools: weights_sensitivity

## workspace_snapshot（工作空间快照）
- status: native；version: 1.0
- algorithm `workspace.snapshot.durable`（priority=10）→ tools: save_workspace_snapshot, restore_workspace_snapshot

## workspace_state_inspection（工作空间状态检视）
- status: native；version: 1.0
- algorithm `workspace.inspection.readonly`（priority=10）→ tools: describe_workspace, list_workspace_snapshots

## zonal_statistics（分区统计）
- status: native；version: 1.0
- input: polygon_feature_set, raster_surface
- output: stats_table
- algorithm `remote.zonal_stats`（priority=20；scientific=VALIDATED）→ tools: zonal_stats

