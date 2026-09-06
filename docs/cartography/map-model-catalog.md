# Map Model Catalog

> 由 `app.lib.cartography.catalog_docs` 从 MapModelRegistry 生成；
> 手改无效。真值：`model_library.py` + `model_packs/`（ADR-0101）。

共 50 个模型（native 31 / planned 19）。

| id | 名称 | 几何 | 图层 | 分级 | 色系 | 默认色带 | 状态 | 降级 |
|---|---|---|---|---|---|---|---|---|
| accessibility_network | 可达性网络图 | line | line | graduated | perceptual_uniform | Viridis | planned | — |
| administrative_aggregation | 行政区聚合参考层 | polygon | fill | graduated | sequential | Blues | native | — |
| administrative_choropleth | 行政分级统计图 | polygon | fill | graduated | sequential | YlOrRd | native | — |
| aggregate_grid | 格网聚合图（H3/渔网） | point | fill | graduated | sequential | YlOrRd | native | — |
| anomaly_surface | 距平（异常场）图 | raster | raster | graduated | diverging | RdBu | planned | — |
| before_after_swipe | 前后对比卷帘 | raster | raster | none | none | — | planned | — |
| bivariate_choropleth | 双变量分级统计图 | polygon | fill | graduated | none | — | planned | — |
| categorical_thematic | 分类专题图 | point/polygon/line | fill | categorical | qualitative | Set1 | native | — |
| categorized_line | 分类线图 | line | line | categorical | qualitative | Dark2 | native | — |
| categorized_point | 分类点图 | point | circle | categorical | qualitative | Set1 | native | — |
| change_comparison_map | 变化对比图 | polygon | fill | graduated | diverging | RdBu | native | diverging_choropleth |
| classified_raster | 分级栅格图 | raster | raster | graduated | sequential | YlOrRd | planned | — |
| dasymetric_map | 分区密度图（dasymetric） | polygon | fill | graduated | sequential | YlOrRd | planned | — |
| diverging_choropleth | 发散分级统计图 | polygon | fill | graduated | diverging | RdBu | native | administrative_choropleth |
| dot_density_map | 点密度图（dot density） | polygon | circle | none | none | — | planned | — |
| elevation_tint_hillshade | 高程分层设色 + 晕渲 | raster | raster | graduated | sequential | Oranges | planned | — |
| equity_assessment | 公平性评估图 | polygon | fill | graduated | diverging | RdBu | native | diverging_choropleth |
| extrusion_3d | 3D 挤出柱状图 | polygon | fill-extrusion | none | sequential | Oranges | native | — |
| flow_od_arc | OD 流向图 | line | line | none | sequential | Plasma | native | — |
| graduated_line | 分级线图 | line | line | graduated | sequential | Blues | native | flow_od_arc |
| graduated_point | 分级点图 | point | circle | graduated | sequential | Blues | native | graduated_symbol |
| hillshade | 山体阴影 | raster | hillshade | none | none | — | planned | — |
| hotspot_overlay | 热点显著性图层 | point/polygon | fill | categorical | diverging | RdBu | native | — |
| isoline_contour | 等值线/等值面 | line/polygon/point/raster | line | none | sequential | Inferno | native | — |
| mcda_score_map | MCDA 评分图 | polygon | fill | graduated | sequential | Purples | native | administrative_choropleth |
| network_centrality_map | 网络中心性图 | point/line | line | graduated | perceptual_uniform | Magma | planned | — |
| normalized_choropleth | 归一化分级统计图 | polygon | fill | graduated | sequential | Blues | native | administrative_choropleth |
| point_cluster | 点聚类图 | point | circle | none | sequential | Blues | planned | — |
| point_overlay | 点叠加层 | point | circle | none | none | — | native | — |
| proportional_symbol | 比例符号图（气泡图） | point | circle | none | sequential | Blues | native | — |
| proximity_overlay | 邻近/缓冲叠加 | point/line/polygon | fill | none | qualitative | Set2 | native | — |
| raster_surface | 栅格连续色面 | raster | raster | none | perceptual_uniform | Viridis | native | — |
| risk_exposure_classes | 风险暴露分级图 | polygon | fill | graduated | sequential | Reds | native | administrative_choropleth |
| route_map | 路径图 | line | line | none | qualitative | Set1 | planned | — |
| sar_change_detection | SAR 变化检测图 | raster | raster | graduated | diverging | RdBu | planned | — |
| sar_intensity_surface | SAR 强度面 | raster | raster | none | perceptual_uniform | Inferno | planned | — |
| sensitivity_analysis_presentation | 敏感性分析呈现图 | polygon | fill | graduated | diverging | RdBu | planned | — |
| service_area_overlay | 服务区叠加 | polygon | fill | graduated | sequential | Blues | native | proximity_overlay |
| simple_point_map | 轻量点图 | point | circle | none | none | — | native | — |
| site_selection_result | 选址评价结果图 | polygon | fill | graduated | sequential | Greens | native | administrative_choropleth |
| spectral_index_surface | 光谱指数面 | raster | raster | none | perceptual_uniform | Viridis | native | raster_surface |
| suitability_classes | 适宜性分级图 | polygon | fill | graduated | sequential | Greens | native | administrative_choropleth |
| temporal_trend_surface | 时序趋势面 | raster | raster | graduated | diverging | RdBu | planned | — |
| terrain_analytical_surface | 地形解析面 | raster | raster | none | perceptual_uniform | Magma | native | raster_surface |
| uncertainty_choropleth | 不确定性分级统计图 | polygon | fill | graduated | sequential | Purples | planned | — |
| uncertainty_point_symbol | 不确定性点符号 | point | circle | graduated | sequential | Purples | planned | — |
| uncertainty_surface | 不确定性面 | raster | raster | none | sequential | Purples | planned | — |
| visual_heatmap | 视觉热力图 | point | heatmap | none | perceptual_uniform | classic | native | — |
| vulnerability_index | 脆弱性指数图 | polygon | fill | graduated | sequential | Oranges | native | administrative_choropleth |
| zoning_planning | 区划/规划用地图 | polygon | fill | categorical | qualitative | Pastel1 | native | categorical_thematic |

## planned 模型的诚实披露

### accessibility_network（可达性网络图）
- planned：需要可达性计算 artifact（机会累积/引力模型），本分支未实现

### anomaly_surface（距平（异常场）图）
- planned：需要背景态参考 artifact，本分支未实现

### before_after_swipe（前后对比卷帘）
- planned：runtime 无 swipe 交互语义与导出双帧契约，本分支未实现
- 导出侧等价物是双帧并排（before/after 双面板），swipe 仅限交互面

### bivariate_choropleth（双变量分级统计图）
- planned：paint 投影与图例（3×3 色阵 legend）未实现，不伪装 native；色阵族未建，缺省色带留空
- 双变量图读者负荷高 —— 仅在两变量确有交互语义时使用

### classified_raster（分级栅格图）
- planned：栅格 step 化着色需 color-relief/服务端重分类链路，本分支未实现

### dasymetric_map（分区密度图（dasymetric））
- planned：需要控制层数据契约与重分配算法，本分支未实现

### dot_density_map（点密度图（dot density））
- planned：需要按面单元比例约束的确定性撒点算法（本分支未实现，不伪装 native）
- 撒点位置是示意性重分布，不是真实位置 —— 图例必须披露『Random dot within polygon』

### elevation_tint_hillshade（高程分层设色 + 晕渲）
- planned：依赖 hillshade（未接线）与多层栅格合成顺序契约
- 受限于已注册色带库存，以 Oranges 近似经典 hypsometric 多色分层方案

### hillshade（山体阴影）
- planned：hillshade 图层在前端编译器 union 与 raster-dem source 链路均未接线
- 光源方位角/高度角必须随图披露，否则同一 DEM 可渲染出不同地貌观感

### network_centrality_map（网络中心性图）
- planned：需要中心性计算 artifact（本分支网络算法未含 centrality）
- 中心性对网络边界截断极敏感 —— 截断窗口必须在披露组件声明
- 节点+边联合编码需多层组合；本模型按输入几何族单族渲染

### point_cluster（点聚类图）
- planned：依赖 maplibre source cluster 语义（前端编译器 union 未含 cluster 配置）
- 簇计数是屏幕相关量 —— 同一数据不同缩放簇数不同，导出前固定 zoom

### route_map（路径图）
- planned：需要路由结果 artifact 契约（turn-by-turn/成本字段），本分支未实现

### sar_change_detection（SAR 变化检测图）
- planned：无 InSAR/相干性 artifact 契约，本分支未实现
- 形变量色标必须对称且以 0 为中点，否则毫米级形变被误读

### sar_intensity_surface（SAR 强度面）
- planned：无 SAR 强度 artifact 类型与 dB 归一契约，本分支未实现

### sensitivity_analysis_presentation（敏感性分析呈现图）
- planned：需要情景/扰动 artifact 契约与多面板组合语义，本分支未实现

### temporal_trend_surface（时序趋势面）
- planned：需要趋势拟合 artifact（斜率/p 值字段），本分支未实现
- 不显著趋势应以低饱和/置灰表达，不与显著趋势争色

### uncertainty_choropleth（不确定性分级统计图）
- planned：需要区间/方差字段契约与 hatch 填充渲染，本分支未实现

### uncertainty_point_symbol（不确定性点符号）
- planned：需要区间/方差字段契约（artifact schema 未定义 uncertainty 列）

### uncertainty_surface（不确定性面）
- planned：需要不确定性场 artifact（方差/分位带），本分支未实现

