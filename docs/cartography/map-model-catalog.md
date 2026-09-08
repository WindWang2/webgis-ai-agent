# Map Model Catalog

> 由 `app.lib.cartography.catalog_docs` 从 MapModelRegistry 生成；
> 手改无效。真值：`model_library.py` + `model_packs/`（ADR-0101）。

共 80 个模型（native 77 / planned 3）。

| id | 名称 | 几何 | 图层 | 分级 | 色系 | 默认色带 | 状态 | 降级 |
|---|---|---|---|---|---|---|---|---|
| accessibility_network | 可达性网络图 | line | line | graduated | perceptual_uniform | Viridis | native | graduated_line |
| administrative_aggregation | 行政区聚合参考层 | polygon | fill | graduated | sequential | Blues | native | — |
| administrative_choropleth | 行政分级统计图 | polygon | fill | graduated | sequential | YlOrRd | native | — |
| aggregate_grid | 格网聚合图（H3/渔网） | point | fill | graduated | sequential | YlOrRd | native | — |
| anomaly_surface | 距平（异常场）图 | raster | raster | graduated | diverging | RdBu | native | change_comparison_map |
| aspect_direction_map | 坡向八方向图 | raster | raster | categorical | qualitative | Set1 | native | classified_raster |
| before_after_swipe | 前后对比卷帘 | raster | raster | none | none | — | planned | — |
| bivariate_choropleth | 双变量分级统计图 | polygon | fill | graduated | none | BiPurpleOrange | native | normalized_choropleth |
| bivariate_raster | 双变量栅格图 | raster | raster | graduated | none | BiPurpleOrange | native | classified_raster |
| cartogram_map | 统计地图变形（cartogram） | polygon | fill | graduated | sequential | YlOrRd | planned | — |
| categorical_thematic | 分类专题图 | point/polygon/line | fill | categorical | qualitative | Set1 | native | — |
| categorized_line | 分类线图 | line | line | categorical | qualitative | Dark2 | native | — |
| categorized_point | 分类点图 | point | circle | categorical | qualitative | Set1 | native | — |
| change_comparison_map | 变化对比图 | polygon | fill | graduated | diverging | RdBu | native | diverging_choropleth |
| classification_result_map | 分类结果图 | polygon | fill | categorical | qualitative | Set2 | native | — |
| classified_raster | 分级栅格图 | raster | raster | graduated | sequential | YlOrRd | native | raster_surface |
| confusion_matrix_map | 误差/混淆可视化图 | polygon | fill | categorical | qualitative | Set1 | native | categorical_thematic |
| dasymetric_map | 分区密度图（dasymetric） | polygon | fill | graduated | sequential | YlOrRd | native | normalized_choropleth |
| dbscan_cluster_map | DBSCAN 聚类簇图 | point | circle | categorical | qualitative | Set1 | native | — |
| distance_surface | 距离场图 | raster | raster | none | sequential | Oranges | native | raster_surface |
| diverging_choropleth | 发散分级统计图 | polygon | fill | graduated | diverging | RdBu | native | administrative_choropleth |
| dot_density_map | 点密度图（dot density） | polygon | circle | none | none | — | native | — |
| elevation_tint_hillshade | 高程分层设色 + 晕渲 | raster | raster | graduated | sequential | Oranges | native | hillshade |
| equity_assessment | 公平性评估图 | polygon | fill | graduated | diverging | RdBu | native | diverging_choropleth |
| extrusion_3d | 3D 挤出柱状图 | polygon | fill-extrusion | none | sequential | Oranges | native | — |
| flow_accumulation_surface | 汇流累积面 | raster | raster | none | sequential | Purples | native | terrain_analytical_surface |
| flow_hub_map | 流量枢纽图 | point | circle | none | sequential | Plasma | native | proportional_symbol |
| flow_od_arc | OD 流向图 | line | line | none | sequential | Plasma | native | — |
| graduated_line | 分级线图 | line | line | graduated | sequential | Blues | native | flow_od_arc |
| graduated_point | 分级点图 | point | circle | graduated | sequential | Blues | native | graduated_symbol |
| gwr_coefficient_map | GWR 局部系数图 | polygon | fill | graduated | diverging | PuOr | native | diverging_choropleth |
| hillshade | 山体阴影 | raster | raster | none | sequential | Gray | native | raster_surface |
| hotspot_overlay | 热点显著性图层 | point/polygon | fill | categorical | diverging | RdBu | native | — |
| huff_probability_surface | Huff 概率面 | raster/polygon | raster | none | sequential | Oranges | native | raster_surface |
| interpolation_result_map | 插值结果面 | raster | raster | none | perceptual_uniform | Viridis | native | raster_surface |
| isoline_contour | 等值线/等值面 | line/polygon/point/raster | line | none | sequential | Inferno | native | — |
| kernel_density_surface | 核密度估计面 | raster | raster | none | perceptual_uniform | Magma | native | raster_surface |
| landform_classification_map | 地貌分类图 | raster | raster | categorical | qualitative | Dark2 | native | classified_raster |
| location_allocation_map | 区位配置图 | point/line/polygon | fill | categorical | qualitative | Dark2 | native | nearest_facility_map |
| mcda_score_map | MCDA 评分图 | polygon | fill | graduated | sequential | Purples | native | administrative_choropleth |
| multi_ring_buffer_map | 多环缓冲图 | polygon | fill | graduated | sequential | Blues | native | proximity_overlay |
| nearest_facility_map | 最近设施归属图 | line/point | line | categorical | qualitative | Set2 | native | categorized_line |
| network_centrality_map | 网络中心性图 | point/line | line | graduated | perceptual_uniform | Magma | native | graduated_line |
| network_flow_map | 网络流量图 | line | line | graduated | perceptual_uniform | Inferno | native | graduated_line |
| normalized_choropleth | 归一化分级统计图 | polygon | fill | graduated | sequential | Blues | native | administrative_choropleth |
| ols_residual_map | 回归残差图 | polygon | fill | graduated | diverging | RdBu | native | diverging_choropleth |
| point_cluster | 点聚类图 | point | circle | none | sequential | Blues | native | — |
| point_overlay | 点叠加层 | point | circle | none | none | — | native | — |
| proportional_symbol | 比例符号图（气泡图） | point | circle | none | sequential | Blues | native | — |
| proximity_overlay | 邻近/缓冲叠加 | point/line/polygon | fill | none | qualitative | Set2 | native | — |
| raster_surface | 栅格连续色面 | raster | raster | none | perceptual_uniform | Viridis | native | — |
| risk_exposure_classes | 风险暴露分级图 | polygon | fill | graduated | sequential | Reds | native | administrative_choropleth |
| route_map | 路径图 | line | line | categorical | qualitative | Set1 | native | categorized_line |
| rs_composite_map | 遥感合成影像图 | raster | raster | none | none | — | native | raster_surface |
| sar_change_detection | SAR 变化检测图 | raster | raster | graduated | diverging | RdBu | native | change_comparison_map |
| sar_intensity_surface | SAR 强度面 | raster | raster | none | perceptual_uniform | Inferno | native | raster_surface |
| sensitivity_analysis_presentation | 敏感性分析呈现图 | polygon | fill | graduated | diverging | RdBu | native | mcda_score_map |
| service_area_overlay | 服务区叠加 | polygon | fill | graduated | sequential | Blues | native | proximity_overlay |
| simple_point_map | 轻量点图 | point | circle | none | none | — | native | — |
| site_selection_result | 选址评价结果图 | polygon | fill | graduated | sequential | Greens | native | administrative_choropleth |
| small_multiple_map | 小倍数地图组 | polygon/point/raster | fill | graduated | sequential | Blues | planned | — |
| spectral_index_surface | 光谱指数面 | raster | raster | none | perceptual_uniform | Viridis | native | raster_surface |
| stream_order_map | 河网分级图 | line | line | graduated | sequential | Blues | native | graduated_line |
| suitability_classes | 适宜性分级图 | polygon | fill | graduated | sequential | Greens | native | administrative_choropleth |
| suitability_constraint_overlay | 硬约束掩膜叠加 | polygon | fill | categorical | qualitative | Set1 | native | proximity_overlay |
| surface_difference_map | 表面差值图 | raster | raster | graduated | diverging | RdBu | native | change_comparison_map |
| temporal_comparison_map | 时相对比双专题图 | polygon | fill | graduated | sequential | Blues | native | change_comparison_map |
| temporal_trend_surface | 时序趋势面 | raster | raster | graduated | diverging | RdBu | native | change_comparison_map |
| terrain_analytical_surface | 地形解析面 | raster | raster | none | perceptual_uniform | Magma | native | raster_surface |
| twi_map | 地形湿度指数图 | raster | raster | none | sequential | Blues | native | terrain_analytical_surface |
| uncertainty_choropleth | 不确定性分级统计图 | polygon | fill | graduated | sequential | Purples | native | normalized_choropleth |
| uncertainty_point_symbol | 不确定性点符号 | point | circle | graduated | sequential | Purples | native | graduated_point |
| uncertainty_surface | 不确定性面 | raster | raster | none | sequential | Purples | native | raster_surface |
| viewshed_map | 视线域/可视域图 | raster | raster | categorical | qualitative | Set1 | native | classified_raster |
| visual_heatmap | 视觉热力图 | point | heatmap | none | perceptual_uniform | classic | native | — |
| voronoi_partition_map | Voronoi 分割图 | polygon | fill | categorical | qualitative | Pastel1 | native | categorical_thematic |
| vulnerability_index | 脆弱性指数图 | polygon | fill | graduated | sequential | Oranges | native | administrative_choropleth |
| watershed_boundary_map | 流域边界图 | polygon | fill | categorical | qualitative | Set2 | native | categorical_thematic |
| weighted_overlay_surface | 加权叠加分析面 | raster | raster | none | sequential | Greens | native | raster_surface |
| zoning_planning | 区划/规划用地图 | polygon | fill | categorical | qualitative | Pastel1 | native | categorical_thematic |

## planned 模型的诚实披露

### before_after_swipe（前后对比卷帘）
- planned：runtime 无 swipe 交互语义与导出双帧契约，本分支未实现
- V5（ADR-0118 D7）部分落地：live swipe 对比的静态导出现已显式组合（comparison_export_composed）或诚实披露（comparison_second_view_not_exported），不再静默丢第二视图
- 导出侧等价物是双帧并排（before/after 双面板），swipe 仅限交互面

### cartogram_map（统计地图变形（cartogram））
- planned：需要面积保持变形算法（Gastner-Newman 扩散等）与变形后几何的渲染契约，本分支未实现
- 变形图必须同时披露原始地理轮廓参照（inset），否则读者失去地理定位
- V5（ADR-0118 D8）：导出请求携带 cartogram 意图时诚实降级（cartogram_unsupported，按未变形几何渲染）—— 不伪造变形支持

### small_multiple_map（小倍数地图组）
- planned：需要多画幅组合运行时（多个 MapSpec 画面的并置/联动布局），当前单画布 MapSpec 无法承载 —— 诚实保留 planned
- V5（ADR-0118 D8）导出侧最小真实闭环：ExportRequest.frames 多帧运行时（逐帧 filter/extent 确定性执行 → pdf pages / png grid 拼板）可作为小倍数导出承载面；spec 级多画幅模型仍待后续 Epic

