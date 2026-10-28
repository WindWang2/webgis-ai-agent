# INTERFACE_CONTRACTS — g3dv2-c-analysis-harness-map

## 新增/扩展的生产接口

### 1. app/lib/geo_analysis/viewshed_geometry.py（纯算子）

- `raster_mask_polygons(mask, transform, *, valid=None, max_polygons=48,
  max_total_vertices=20000, kind="raster_mask") -> (FeatureCollection, meta)`
  meta 键：mask_cells/polygon_count/attributed_cells/unattributed_cells/
  dropped_polygon_count/area_threshold_cells/vertex_count/polygon_area_crs_units2
- `visibility_geometry(visible, valid, transform, *, cell_size,
  cell_size_x=None, ...) -> (FeatureCollection, meta)`
  meta 增：visible_cells/valid_cells/valid_coverage_fraction/
  attributed_visible_area_m2/unattributed_visible_area_m2/
  geometry_attribution_fraction/effective_resolution_m

### 2. app/lib/geo_analysis/volumes.py（纯算子）

- `cut_fill_volume(dem, cell_size, cell_size_x=None, *, mask=None,
  reference="constant"|"mean", base_z=None, nodata=None,
  max_invalid_fraction=0.5, z_units="m", vertical_datum="unknown",
  override_vertical_datum=None) -> (stats, meta)`
  stats 键：reference_mode/base_z/cut_volume_m3/fill_volume_m3/net_volume_m3/
  cut_cells/fill_cells/grade_cells/usable_cells/region_cells/
  planar_region_area_m2/cut_depth_max_m/fill_depth_max_m/cut_depth_mean_m/
  fill_depth_mean_m；meta 含 boundary_cells/cell_area_m2/sign_convention/method

### 3. 工具面

- `viewshed_analysis` v2 契约：+include_geometry(boolean, default True)；
  output_semantic_type stats→geojson_fc；stats 字段两模式逐位一致
- `cut_fill_volume_analysis`（新）：raster_path/polygon/reference/base_z/
  max_invalid_fraction/vertical_datum/override_vertical_datum/z_units/
  include_geometry；产物 site+cut_region+fill_region GeoJSON

### 4. dispatch 认可键（沿 #517/uncertainty 纪律）

- 工具结果 `profile_chart: {chart: ChartData}` → 缝铸 `ref:profile-*`
  （prefix=profile）→ `result["profile_ref"]`、内联体剥离、H-1 哨兵检查
- `_author_display_result`：profile_ref 存在 → elevation-profile 组件
  upsert（profileRef/unit/verticalDatum）→ profile_component ACK 键

### 5. run_3d_analysis（签名扩展，向后兼容）

- analysis ∈ {viewshed, line_of_sight, terrain_profile, surface_distance,
  surface_area, cut_fill_volume}（其余 decline）
- 新参数：target_x/target_y/vertices/polygon/base_z/reference/options(JSON)/
  observer_height/target_height/max_distance（兼容保留）
- 返回新增：staleness_binding_ack / prior_stale_analyses /
  result_geojson_hoisted + geojson（提升位）/ profile_chart（剖面族）

### 6. analysis_binding_store（会话状态面）

- 键：`_scene3d_analysis_bindings`（后端私有，恢复面不下发）
- `register_analysis_binding(session_id, binding)`（FIFO ≤8，幂等，
  选择上下文戳记）；`load_analysis_bindings`；
  `evaluate_session_staleness(session_id, *, mapspec=None,
  dem_source_ids=None)`（无 spec 上下文跳过 dem 检查）

### 7. staleness 语义（G3D04 纯函数微调）

- 选择漂移检查适用域：goal ∈ {viewshed, line_of_sight} 或显式
  observer_feature_id；其余 goal 不进选择检查（DEM/显隐检查不变）

### 8. 组合模板（composition_packs/analysis_products.py）

- composition.mountain_los_obstruction（interactive+png；
  scene_disclosure/stats required）
- composition.route_profile_joint（interactive+png+pdf+svg——纯 2D；
  elevation_profile/stats required；模型仅 graduated_line）
- composition.earthwork_cut_fill（interactive+png；
  scene_disclosure/methodology_note/stats required）

### 9. 验收基建（测试面）

- fixtures_data：raster_dem_cone / raster_dem_nodata（EPSG:32648 锥形）
- StepKind.MAPSPEC_SOURCE：{source_id, source} → 真实 UpsertSourceIntent
- TaskEvidence 面：scene3d_analysis.{binding_count, analysis_goals,
  stale_reason_codes, all_fresh, evaluations}

### 10. 端点

- GET /sessions/{id}/chart-artifacts/{ref}：白名单 (ref:chart-, ref:profile-)；
  载荷协议不变（{chart: ChartData}）；其余前缀仍 400
