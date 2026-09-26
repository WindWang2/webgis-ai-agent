# Execution Catalog · Recipes

> recipe 的 capability 引用与降级链（capability-first，非工具名）。

## accessibility_analysis（可达性/服务区分析）
- capabilities: poi_query, point_profile, service_area
- fallback_links: proximity_analysis

## admin_feature_audit（行政区划要素清查）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, poi_query

## admin_ranking_stats（行政区排名统计）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, poi_query
- geometry: MultiPolygon, Polygon

## administrative_choropleth（行政分级统计图）
- capabilities: admin_aggregation, admin_boundary_query, analytical_density, poi_query, point_profile；optional: analytical_density

## agriculture_suitability（农业适宜性评价）
- capabilities: geometry_overlay, mcda_evaluation, ndvi, raster_reclassify, raster_source, terrain_slope, zonal_statistics

## air_quality_admin_stats（行政区空气质量统计）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, temporal_aggregate, temporal_profile
- fallback_links: administrative_choropleth, poi_distribution_overview

## air_quality_surface（空气质量插值面）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, point_profile, spatial_interpolation, zonal_statistics；optional: admin_aggregation, admin_boundary_query
- geometry: MultiPoint, Point

## aspect_analysis_workflow（坡向分析）
- capabilities: raster_source, terrain_aspect, terrain_derivatives

## basin_pour_point_stats（出口断面流域统计）
- capabilities: admin_boundary_query, point_profile, raster_source, terrain_hydrology, zonal_statistics

## bitemporal_raster_change（两期栅格变化检测）
- capabilities: raster_change_detection, raster_source, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## builtup_index_screening（不透水面/建成区指数筛查）
- capabilities: band_math, raster_reclassify, raster_source, spectral_index

## categorical_composition_stats（类别构成统计）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, poi_query
- geometry: MultiPoint, Point
- fallback_links: administrative_choropleth, poi_distribution_overview

## categorical_distribution（分类分布专题）
- capabilities: category_breakdown, poi_query, point_profile
- fallback_links: poi_distribution_overview

## change_area_accounting（变化面积台账）
- capabilities: admin_aggregation, admin_boundary_query, raster_change_detection, raster_source, rate_aggregation, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## changepoint_detection_workflow（突变点检测）
- capabilities: poi_query, temporal_change_point, temporal_profile, temporal_trend
- fallback_links: administrative_choropleth, poi_distribution_overview

## clinic_coverage_analysis（基层医疗覆盖分析）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, rate_aggregation, service_area

## closest_facility_assignment（最近设施分配）
- capabilities: admin_aggregation, admin_boundary_query, closest_facility, poi_query, point_profile；optional: admin_aggregation, admin_boundary_query

## commute_flow_corridor（通勤流走廊分析）
- capabilities: od_flow_mapping, od_matrix, poi_query, point_profile
- fallback_links: administrative_choropleth, poi_distribution_overview

## construction_suitability（建设适宜性评价）
- capabilities: admin_aggregation, admin_boundary_query, geometry_overlay, mcda_evaluation, raster_reclassify, raster_source, terrain_slope, zonal_statistics；optional: admin_aggregation, admin_boundary_query

## contour_map_product（等高线产品）
- capabilities: raster_source, terrain_contours, terrain_derivatives

## convex_hull_extent（分布范围圈定）
- capabilities: convex_hull, poi_query, point_profile
- geometry: MultiPoint, Point

## cultivated_land_distribution（耕地分布清查）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, raster_reclassify, raster_source, zonal_statistics

## cultural_facility_distribution（文体设施分布）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, point_profile
- geometry: MultiPoint, Point

## density_grid_fishnet（渔网格网密度）
- capabilities: admin_boundary_query, grid_binning, poi_query, point_profile
- geometry: MultiPoint, Point

## density_grid_h3（H3 六边形格网密度）
- capabilities: admin_aggregation, grid_binning, poi_query, point_profile；optional: admin_aggregation
- geometry: MultiPoint, Point

## density_hotspot_screening（热点初筛）
- capabilities: grid_binning, hotspot, kde_density, poi_query, point_profile；optional: hotspot
- geometry: MultiPoint, Point

## density_kde_surface（KDE 核密度面）
- capabilities: density_surface, kde_density, poi_query, point_profile
- geometry: MultiPoint, Point

## density_quantitative_per_area（定量密度分析）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, point_profile, rate_aggregation

## density_visual_overview（视觉密度概览）
- capabilities: admin_aggregation, poi_query, point_profile；optional: admin_aggregation
- geometry: MultiPoint, Point

## disaster_shelter_accessibility（避难场所可达保障）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, rate_aggregation, service_area
- fallback_links: administrative_choropleth, poi_distribution_overview

## drainage_density_stats（排水分区密度统计）
- capabilities: admin_aggregation, admin_boundary_query, raster_source, rate_aggregation, terrain_hydrology

## earthquake_exposure_screening（地震暴露筛查）
- capabilities: admin_aggregation, admin_boundary_query, geometry_buffer, geometry_overlay, multi_ring_buffer

## ecological_suitability（生态保护适宜性）
- capabilities: admin_boundary_query, geometry_overlay, mcda_evaluation, ndvi, raster_reclassify, raster_source, terrain_slope, zonal_statistics

## edu_facility_distribution（教育设施分布概览）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, poi_query, point_profile；optional: category_breakdown
- geometry: MultiPoint, Point

## education_equity_per_capita（教育资源人均公平性）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, poi_query, rate_aggregation

## emergency_equity_coverage（应急服务均衡覆盖）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, rate_aggregation, service_area

## emergency_resource_dispatch（应急资源调度分析）
- capabilities: closest_facility, poi_query, point_profile, service_area

## emergency_response_coverage（应急响应覆盖）
- capabilities: admin_aggregation, admin_boundary_query, closest_facility, poi_query, service_area
- fallback_links: administrative_choropleth, poi_distribution_overview

## emergency_shelter_distribution（应急避难场所分布）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, point_profile
- geometry: MultiPoint, Point

## env_sensitivity_zoning（环境敏感区划）
- capabilities: geometry_buffer, geometry_overlay, raster_reclassify, raster_source, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## epidemic_density_monitor（疫情密度监测）
- capabilities: grid_binning, kde_density, poi_query, point_profile, temporal_profile
- geometry: MultiPoint, Point

## ev_charger_site_selection（充电站选址评价）
- capabilities: admin_boundary_query, geometry_buffer, mcda_evaluation, poi_query, traffic_status

## extrusion_3d_thematic（3D 挤出立体专题图）
- capabilities: admin_aggregation, point_profile
- geometry: MultiPolygon, Polygon

## facility_coverage_ratio（设施覆盖率评价）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, rate_aggregation, service_area

## facility_exposure_inventory（设施暴露清单）
- capabilities: category_breakdown, geometry_buffer, geometry_overlay, poi_query, spatial_join
- fallback_links: administrative_choropleth, poi_distribution_overview

## facility_per_capita_profile（设施人均画像）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, point_profile, rate_aggregation

## financial_branch_distribution（金融网点分布）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, point_profile
- geometry: MultiPoint, Point

## flood_extent_screening（淹没范围初筛）
- capabilities: admin_boundary_query, poi_query, raster_reclassify, raster_source, terrain_derivatives, terrain_hydrology；optional: admin_boundary_query, poi_query

## flood_inundation_screen（内涝影响筛查）
- capabilities: category_breakdown, geometry_buffer, multi_ring_buffer, poi_query, spatial_join
- fallback_links: administrative_choropleth, poi_distribution_overview

## flood_risk_assessment（洪水风险评估）
- capabilities: admin_aggregation, admin_boundary_query, geometry_overlay, poi_query, raster_reclassify, raster_source, terrain_hydrology, zonal_statistics；optional: admin_aggregation, admin_boundary_query

## flow_accumulation_mapping（汇流累积制图）
- capabilities: raster_source, terrain_derivatives, terrain_hydrology

## forest_orchard_inventory（林园地资源清查）
- capabilities: admin_aggregation, admin_boundary_query, ndvi, raster_reclassify, raster_source, spectral_index, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## geary_global_autocorrelation（全局 Geary's C 检验）
- capabilities: admin_aggregation, admin_boundary_query, global_gearys_c, global_morans_i, poi_query；optional: global_morans_i
- geometry: MultiPolygon, Polygon

## general_g_cluster_analysis（General G 聚集度分析）
- capabilities: admin_aggregation, admin_boundary_query, general_g, poi_query
- geometry: MultiPolygon, Polygon

## generic_mcda_site_ranking（通用多准则选址）
- capabilities: admin_boundary_query, geometry_buffer, mcda_evaluation, poi_query, proximity_buffer, raster_reclassify, zonal_statistics；optional: raster_reclassify, zonal_statistics

## geological_hazard_inventory（地质灾害点清单）
- capabilities: admin_aggregation, admin_boundary_query, kde_density, poi_query, point_profile

## getis_ord_hotspot_significance（Getis-Ord Gi* 显著性热点）
- capabilities: admin_aggregation, admin_boundary_query, getis_ord_gi_star, hotspot, kde_density, poi_query；optional: hotspot, kde_density
- geometry: MultiPolygon, Polygon

## global_moran_autocorrelation（全局空间自相关（Moran's I））
- capabilities: admin_aggregation, admin_boundary_query, global_morans_i, local_morans_i, poi_query；optional: local_morans_i
- geometry: MultiPolygon, Polygon

## grassland_condition_index（草地状况评价）
- capabilities: admin_boundary_query, ndvi, raster_source, spectral_index, temporal_trend, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## green_space_distribution（公园绿地分布）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, point_profile, zonal_statistics；optional: zonal_statistics

## greening_rate_admin（绿化率统计）
- capabilities: admin_aggregation, admin_boundary_query, ndvi, raster_source, rate_aggregation, zonal_statistics

## grid_density_aggregate（格网聚合密度）
- capabilities: admin_boundary_query, grid_binning, poi_query, point_profile；optional: admin_boundary_query
- geometry: MultiPoint, Point

## hazard_buffer_receptor_screen（危险源缓冲受体筛查）
- capabilities: category_breakdown, geometry_buffer, multi_ring_buffer, poi_query, spatial_join
- fallback_links: administrative_choropleth, poi_distribution_overview

## hazard_exposure_overlay（通用危险-暴露叠加）
- capabilities: admin_aggregation, admin_boundary_query, geometry_buffer, geometry_overlay, spatial_join

## health_resource_per_capita（医疗资源人均画像）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, poi_query, rate_aggregation

## healthcare_equity_access（医疗可达公平性）
- capabilities: admin_aggregation, admin_boundary_query, global_morans_i, poi_query, rate_aggregation, service_area

## healthcare_facility_distribution（医疗设施分布概览）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, poi_query, point_profile；optional: category_breakdown
- geometry: MultiPoint, Point

## hillshade_cartography（山体阴影渲染）
- capabilities: raster_source, terrain_derivatives, terrain_hillshade

## hospital_service_area_stats（医院服务区统计）
- capabilities: admin_aggregation, admin_boundary_query, geometry_overlay, poi_query, point_profile, service_area
- fallback_links: administrative_choropleth, poi_distribution_overview

## hospital_site_selection（医院选址评价）
- capabilities: admin_boundary_query, closest_facility, mcda_evaluation, poi_query, service_area

## hotspot_analysis（热点分析产品）
- capabilities: hotspot, kde_density, poi_query
- geometry: MultiPoint, Point
- fallback_links: point_density

## idw_interpolation_workflow（IDW 反距离加权插值）
- capabilities: poi_query, point_profile, spatial_interpolation
- geometry: MultiPoint, Point

## index_time_series_trend（指数时序趋势）
- capabilities: raster_source, spectral_index, temporal_profile, temporal_trend
- fallback_links: administrative_choropleth, poi_distribution_overview

## infrastructure_node_distribution（市政节点设施分布）
- capabilities: admin_boundary_query, category_breakdown, poi_query, point_profile
- geometry: MultiPoint, Point

## insar_deformation_screening（InSAR 形变筛查）
- capabilities: raster_source, sar_analysis, sar_radiometric_calibration；optional: sar_radiometric_calibration
- fallback_links: administrative_choropleth, poi_distribution_overview

## interannual_comparison_workflow（年际对比分析）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, temporal_aggregate, temporal_profile
- fallback_links: administrative_choropleth, poi_distribution_overview

## interpolation_cv_compare（插值方法交叉验证比较）
- capabilities: poi_query, point_profile, spatial_interpolation
- geometry: MultiPoint, Point

## interpolation_uncertainty_map（插值不确定性图）
- capabilities: poi_query, point_profile, spatial_interpolation
- geometry: MultiPoint, Point

## isoline_contour_map（等值线/等值面专题图）
- capabilities: kde_density, point_profile

## kriging_interpolation_workflow（克里金插值面）
- capabilities: admin_boundary_query, poi_query, point_profile, spatial_interpolation, zonal_statistics；optional: admin_boundary_query, zonal_statistics
- geometry: MultiPoint, Point

## landcover_area_accounting（地类面积台账）
- capabilities: admin_aggregation, admin_boundary_query, raster_reclassify, raster_source, rate_aggregation, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## landcover_categorical_map（地表覆盖分类图）
- capabilities: admin_aggregation, admin_boundary_query, raster_reclassify, raster_source, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## landcover_change_inventory（地类变化台账）
- capabilities: admin_aggregation, admin_boundary_query, raster_change_detection, raster_source, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## landfill_site_screening（填埋场选址筛查）
- capabilities: admin_boundary_query, geometry_buffer, geometry_overlay, mcda_evaluation, multi_ring_buffer, poi_query

## landmark_simple_view（地标要素轻量查看）
- capabilities: poi_query, point_profile
- geometry: MultiPoint, Point

## landslide_risk_assessment（滑坡风险评估）
- capabilities: admin_aggregation, admin_boundary_query, geometry_overlay, poi_query, raster_reclassify, raster_source, terrain_slope, zonal_statistics；optional: admin_aggregation, admin_boundary_query

## landuse_structure_admin（用地结构统计）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, raster_reclassify, raster_source, zonal_statistics

## linear_trend_analysis（线性趋势分析）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, temporal_profile, temporal_trend
- fallback_links: administrative_choropleth, poi_distribution_overview

## local_moran_lisa（局部自相关聚类图（LISA））
- capabilities: admin_aggregation, admin_boundary_query, global_morans_i, local_morans_i, poi_query；optional: global_morans_i
- geometry: MultiPolygon, Polygon

## location_allocation_planning（选址-分配优化）
- capabilities: admin_boundary_query, closest_facility, location_allocation, poi_query, point_profile
- fallback_links: administrative_choropleth, poi_distribution_overview

## logistics_facility_distribution（物流网点分布）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, point_profile
- geometry: MultiPoint, Point

## monitoring_station_coverage（监测站点覆盖）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, point_profile, service_area；optional: service_area

## multi_hazard_composite（多灾种复合筛查）
- capabilities: band_math, geometry_overlay, raster_reclassify, raster_source, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## nbr_burn_severity（NBR 火烧迹地指数）
- capabilities: band_math, raster_change_detection, raster_source, spectral_index

## ndvi_change_trend（植被变化趋势）
- capabilities: ndvi, raster_change_detection, raster_source, spectral_index, temporal_trend, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## ndvi_vegetation_monitor（NDVI 植被监测）
- capabilities: admin_aggregation, admin_boundary_query, ndvi, raster_source, spectral_index, zonal_statistics；optional: admin_aggregation, admin_boundary_query

## ndwi_water_index（NDWI 水体指数）
- capabilities: band_math, raster_source, spectral_index, zonal_statistics

## nightlight_vitality_profile（夜间灯光活力画像）
- capabilities: admin_aggregation, admin_boundary_query, raster_source, temporal_profile, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## noise_buffer_screen（噪声影响筛查）
- capabilities: category_breakdown, geometry_buffer, multi_ring_buffer, poi_query, spatial_join
- fallback_links: administrative_choropleth, poi_distribution_overview

## od_corridor_mapping（OD 走廊图）
- capabilities: od_flow_mapping, od_matrix, poi_query, point_profile
- fallback_links: administrative_choropleth, poi_distribution_overview

## od_flow_overview（OD 出行流向图）
- capabilities: admin_boundary_query, od_flow_mapping, od_matrix, point_profile；optional: admin_boundary_query
- fallback_links: administrative_choropleth, poi_distribution_overview

## od_matrix_analysis（OD 矩阵分析）
- capabilities: od_flow_mapping, od_matrix, poi_query, point_profile；optional: od_flow_mapping
- fallback_links: administrative_choropleth, poi_distribution_overview

## optical_bitemporal_change（双时相光学变化检测）
- capabilities: admin_aggregation, admin_boundary_query, raster_change_detection, raster_source, zonal_statistics；optional: admin_aggregation, admin_boundary_query
- fallback_links: administrative_choropleth, poi_distribution_overview

## park_access_equity（公园绿地可达公平）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, rate_aggregation, service_area

## poi_distribution_overview（POI 分布概览）
- capabilities: admin_aggregation, admin_boundary_query, hotspot, kde_density, poi_query, point_profile；optional: hotspot, kde_density
- geometry: MultiPoint, Point

## poi_function_mix（POI 功能混合度）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, grid_binning, poi_query
- fallback_links: administrative_choropleth, poi_distribution_overview

## poi_inventory_catalog（POI 要素清单）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, poi_query
- geometry: MultiPoint, Point

## poi_temporal_comparison（POI 两期对比）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, spatial_join, temporal_profile
- fallback_links: administrative_choropleth, poi_distribution_overview

## point_density（点密度/热点分析）
- capabilities: admin_aggregation, hotspot, kde_density, poi_query, point_profile；optional: admin_aggregation
- geometry: MultiPoint, Point

## point_pattern_distance（最近邻距离格局）
- capabilities: poi_query, point_pattern_analysis, point_profile
- geometry: MultiPoint, Point

## point_pattern_quadrat（点格局检验（象限法））
- capabilities: poi_query, point_pattern_analysis, point_profile
- geometry: MultiPoint, Point

## pollution_source_buffer_screen（污染源缓冲筛查）
- capabilities: category_breakdown, geometry_buffer, multi_ring_buffer, poi_query, spatial_join
- fallback_links: administrative_choropleth, poi_distribution_overview

## population_exposure_estimate（人口暴露估算）
- capabilities: admin_aggregation, admin_boundary_query, geometry_buffer, geometry_overlay, rate_aggregation

## proportional_symbol_map（比例符号（气泡）地图）
- capabilities: poi_query, point_profile

## proximity_analysis（邻近/缓冲分析）
- capabilities: poi_query, point_profile, proximity_buffer
- fallback_links: poi_distribution_overview

## raster_distribution（栅格面分布）
- capabilities: ndvi, point_profile, raster_change_detection, raster_source
- fallback_links: administrative_choropleth, poi_distribution_overview

## rate_aggregation_choropleth（比率归一化专题图）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, rate_aggregation
- geometry: MultiPolygon, Polygon

## resource_change_detection（资源变化监测）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, raster_change_detection, raster_source, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## risk_exposure（风险暴露评价）
- capabilities: admin_aggregation, poi_query, proximity_buffer, service_area, spatial_join, zonal_statistics；optional: service_area, zonal_statistics

## road_network_inventory（路网要素清单）
- capabilities: admin_aggregation, admin_boundary_query, category_breakdown, poi_query, point_profile
- fallback_links: administrative_choropleth, poi_distribution_overview

## route_optimization_tour（多点配送路径优化）
- capabilities: poi_query, point_profile, route_optimization, shortest_path；optional: shortest_path
- fallback_links: administrative_choropleth, poi_distribution_overview

## rs_cube_coverage_audit（时序立方体覆盖审计）
- capabilities: rs_cube_alignment, rs_cube_describe

## rs_scene_inventory（影像数据清单）
- capabilities: point_profile, raster_source
- fallback_links: administrative_choropleth, poi_distribution_overview

## rs_temporal_cube_product（遥感时序立方体产品）
- capabilities: raster_source, rs_cube_alignment, rs_cube_describe, rs_joint_fusion, rs_sample_split, rs_temporal_feature_pack, zonal_statistics；optional: rs_sample_split, zonal_statistics

## sar_backscatter_overview（SAR 后向散射概览）
- capabilities: raster_source, sar_analysis, sar_speckle_filtering；optional: sar_speckle_filtering
- fallback_links: administrative_choropleth, poi_distribution_overview

## sar_calibrated_comparison（SAR 定标对比）
- capabilities: raster_source, sar_analysis, sar_radiometric_calibration, sar_speckle_filtering；optional: sar_radiometric_calibration, sar_speckle_filtering

## sar_change_detection_workflow（SAR 变化检测）
- capabilities: raster_change_detection, raster_source, sar_analysis, sar_radiometric_calibration, sar_speckle_filtering；optional: sar_radiometric_calibration, sar_speckle_filtering
- fallback_links: administrative_choropleth, poi_distribution_overview

## sar_flood_mapping（SAR 洪水制图）
- capabilities: raster_reclassify, raster_source, sar_analysis, sar_radiometric_calibration, zonal_statistics；optional: sar_radiometric_calibration
- fallback_links: administrative_choropleth, poi_distribution_overview

## school_site_selection（学校选址评价）
- capabilities: admin_aggregation, admin_boundary_query, geometry_buffer, mcda_evaluation, poi_query, service_area；optional: admin_aggregation

## seasonal_pattern_analysis（季节性模式分析）
- capabilities: poi_query, temporal_aggregate, temporal_profile, temporal_trend
- fallback_links: administrative_choropleth, poi_distribution_overview

## seismic_intensity_exposure_screen（震后影响快速筛查）
- capabilities: admin_aggregation, admin_boundary_query, geometry_buffer, geometry_overlay, multi_ring_buffer
- fallback_links: administrative_choropleth, poi_distribution_overview

## service_area_isochrone（服务区/等时圈）
- capabilities: admin_boundary_query, poi_query, point_profile, service_area；optional: admin_boundary_query

## shelter_site_selection（避难场所选址）
- capabilities: admin_boundary_query, geometry_buffer, mcda_evaluation, poi_query, service_area

## shortest_path_routing（最短路径规划）
- capabilities: external_route_planning, poi_query, point_profile, shortest_path；optional: external_route_planning

## site_selection（选址分析（多准则））
- capabilities: admin_aggregation, admin_boundary_query, mcda_evaluation, poi_query, point_profile, proximity_buffer, service_area, spatial_join；optional: admin_aggregation, poi_query, spatial_join

## slope_analysis_workflow（坡度分析）
- capabilities: admin_aggregation, admin_boundary_query, raster_source, terrain_derivatives, terrain_slope, zonal_statistics；optional: admin_aggregation, admin_boundary_query

## slope_zoning_constraint（坡度分区约束）
- capabilities: admin_aggregation, admin_boundary_query, raster_reclassify, raster_source, terrain_slope, zonal_statistics；optional: admin_aggregation, admin_boundary_query

## spatial_equity（空间公平评价）
- capabilities: admin_aggregation, admin_boundary_query, global_morans_i, local_morans_i, poi_query, point_profile；optional: global_morans_i, local_morans_i

## spatiotemporal_cluster_detection（时空聚类检测）
- capabilities: poi_query, point_profile, spatiotemporal_clustering, temporal_profile
- fallback_links: administrative_choropleth, poi_distribution_overview

## spatiotemporal_emergence_tracking（事件时空涌现追踪）
- capabilities: poi_query, point_profile, spatiotemporal_clustering, temporal_profile
- fallback_links: administrative_choropleth, poi_distribution_overview

## station_catchment_profile（站点集散圈画像）
- capabilities: admin_boundary_query, geometry_buffer, poi_query, point_profile, service_area
- fallback_links: administrative_choropleth, poi_distribution_overview

## station_field_interpolation（站点观测场插值）
- capabilities: poi_query, point_profile, spatial_interpolation, temporal_profile
- geometry: MultiPoint, Point

## stream_network_extraction（河网提取）
- capabilities: raster_source, terrain_derivatives, terrain_hydrology

## suitability_assessment（适宜性评价（加权叠加））
- capabilities: admin_boundary_query, geometry_clip, mcda_evaluation, proximity_buffer, raster_reclassify, raster_source, spatial_join；optional: geometry_clip, raster_source, spatial_join

## temporal_aggregate_stats（时段聚合统计）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, point_profile, temporal_aggregate, temporal_profile
- fallback_links: administrative_choropleth, poi_distribution_overview

## temporal_profile_station（站点时序画像）
- capabilities: poi_query, point_profile, temporal_profile, temporal_trend；optional: temporal_trend
- fallback_links: administrative_choropleth, poi_distribution_overview

## terrain_morphometry_suite（地形形态计量套件）
- capabilities: raster_source, terrain_aspect, terrain_derivatives, terrain_hillshade, terrain_slope

## terrain_relief_overview（地势起伏概览）
- capabilities: admin_aggregation, admin_boundary_query, raster_source, terrain_derivatives, zonal_statistics；optional: admin_aggregation

## tourism_suitability（旅游开发适宜性）
- capabilities: admin_boundary_query, geometry_overlay, mcda_evaluation, poi_query, raster_reclassify, raster_source, service_area, zonal_statistics

## traffic_status_overview（路况状态概览）
- capabilities: poi_query, point_profile, traffic_status
- fallback_links: administrative_choropleth, poi_distribution_overview

## transit_accessibility_workflow（公交可达分析）
- capabilities: poi_query, point_profile, service_area, transit_routing
- fallback_links: administrative_choropleth, poi_distribution_overview

## transit_service_coverage（公交服务覆盖）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, rate_aggregation, service_area
- fallback_links: administrative_choropleth, poi_distribution_overview

## transit_station_distribution（公交地铁站点分布）
- capabilities: admin_boundary_query, grid_binning, poi_query, point_profile
- geometry: MultiPoint, Point

## transit_stop_accessibility（轨道站点步行可达）
- capabilities: admin_boundary_query, poi_query, point_profile, service_area
- fallback_links: administrative_choropleth, poi_distribution_overview

## urban_expansion_monitor（城市扩张监测）
- capabilities: admin_aggregation, admin_boundary_query, band_math, raster_change_detection, raster_source, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## urban_expansion_suitability（城镇扩张适宜性）
- capabilities: admin_boundary_query, band_math, geometry_overlay, mcda_evaluation, raster_reclassify, raster_source, terrain_slope, zonal_statistics

## urban_fire_risk_hotspot（火险热点分析）
- capabilities: geometry_overlay, getis_ord_gi_star, kde_density, poi_query, raster_reclassify
- fallback_links: administrative_choropleth, poi_distribution_overview

## urban_service_density_profile（城区服务密度画像）
- capabilities: admin_aggregation, admin_boundary_query, grid_binning, poi_query, rate_aggregation
- fallback_links: administrative_choropleth, poi_distribution_overview

## viewshed_analysis_workflow（视域/通视分析）
- capabilities: point_profile, raster_source, terrain_derivatives, terrain_viewshed

## voronoi_service_coverage（Voronoi 服务域划分）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, point_profile, voronoi_tessellation；optional: admin_aggregation, admin_boundary_query
- geometry: MultiPoint, Point

## vulnerability_profile_overlay（易损性画像叠加）
- capabilities: admin_aggregation, admin_boundary_query, geometry_buffer, geometry_overlay, rate_aggregation
- fallback_links: administrative_choropleth, poi_distribution_overview

## walking_accessibility_gap（步行可达盲区）
- capabilities: admin_aggregation, admin_boundary_query, poi_query, service_area, spatial_join
- fallback_links: administrative_choropleth, poi_distribution_overview

## water_body_inventory（水域资源清查）
- capabilities: admin_aggregation, admin_boundary_query, raster_reclassify, raster_source, spectral_index, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

## watershed_delineation_workflow（流域划分）
- capabilities: admin_boundary_query, raster_source, terrain_derivatives, terrain_hydrology, zonal_statistics；optional: admin_boundary_query

## zonal_profile_statistics（分区统计画像）
- capabilities: admin_aggregation, admin_boundary_query, raster_source, zonal_statistics

## zonal_rs_index_report（分区遥感指数报表）
- capabilities: admin_aggregation, admin_boundary_query, ndvi, raster_source, spectral_index, zonal_statistics
- fallback_links: administrative_choropleth, poi_distribution_overview

