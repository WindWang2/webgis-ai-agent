# Workflow Catalog（自动生成）

> 本文件由 `python scripts/gen_workflow_catalog.py` 从 RecipeRegistry 生成，
> 请勿手改。唯一事实源：`app/services/gis_harness/recipes.py`（V1 seeds）与
> `app/services/gis_harness/recipe_packs/`（领域包）。

- Registry 总量：**164**（V1 seeds 17 + 领域包 147）
- 领域包：**24** 个
- Registry 内容指纹：`31f767bb9cdda0f4…`

## 领域总览

| 领域 | recipe 数 |
| --- | --- |
| accessibility | 6 |
| change_detection | 6 |
| density | 6 |
| disaster | 5 |
| distribution | 12 |
| environment | 6 |
| equity | 5 |
| exposure | 4 |
| hydrology | 6 |
| interpolation | 5 |
| natural_resources | 6 |
| network | 7 |
| point_pattern | 5 |
| public_health | 4 |
| remote_sensing | 9 |
| risk | 6 |
| sar | 5 |
| site_selection | 6 |
| statistics | 10 |
| suitability | 5 |
| temporal | 6 |
| terrain | 8 |
| transport | 4 |
| urban | 5 |

## accessibility

### `emergency_response_coverage` — 应急响应覆盖

消防/医疗应急响应时间覆盖（响应圈 + 盲区）：阈值口径强披露。

- 任务族：`accessibility_analysis`, `risk_exposure`
- 主制图：`proximity_overlay`；辅：`administrative_choropleth`
- 核心能力：`poi_query`, `service_area`, `closest_facility`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 科学义务：`response_time_semantics`（disclosure → RESPONSE_TIME_SEMANTICS）
- 路由关键词：消防覆盖、急救圈、应急响应、5分钟救援、emergency response coverage、fire coverage
- 内容指纹：`66ab02be21e1b755…`

### `facility_coverage_ratio` — 设施覆盖率评价

行政区设施覆盖率（覆盖人口/总人口）：分母义务，缺人口不得称覆盖率。

- 任务族：`accessibility_analysis`, `spatial_equity`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`point_overlay`
- 核心能力：`poi_query`, `service_area`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）, `denominator`*（block）（`*` = 必选）
- 科学义务：`coverage_ratio_denominator`（denominator → COVERAGE_RATIO_MISSING_DENOMINATOR）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → coverage_area_map（degraded）
- 路由关键词：覆盖率、覆盖人口、覆盖评价、coverage ratio、covered population
- 内容指纹：`c9fdd43ca8cefe27…`

### `location_allocation_planning` — 选址-分配优化

设施区位-分配（location-allocation）规划：容量/需求角色与模型口径披露。

- 任务族：`site_selection`, `accessibility_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `location_allocation`, `closest_facility`, `point_profile`
- 数据角色：`subject`*（block）, `population`, `boundary`*（block）（`*` = 必选）
- 科学义务：`la_model_semantics`（disclosure → LA_MODEL_SEMANTICS）
- 路由关键词：选址分配、设施布局优化、服务点优化、location allocation
- 内容指纹：`39194cb8fd0e4e00…`

### `service_area_isochrone` — 服务区/等时圈

设施服务区（步行/车程 N 分钟等时圈）：路网口径与时间阈值披露。

- 任务族：`accessibility_analysis`, `proximity_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `service_area`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`isochrone_mode_disclosure`（disclosure → ISOCHRONE_MODE_DISCLOSURE）
- 语义回退：`NETWORK_DISCONNECTED` → euclidean_buffer（proxy）
- 路由关键词：服务区、等时圈、15分钟生活圈、步行范围、service area、isochrone
- 内容指纹：`4008241c372554c9…`

### `transit_stop_accessibility` — 轨道站点步行可达

轨道站点 N 分钟步行可达范围与覆盖居住区：接驳口径披露。

- 任务族：`accessibility_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `service_area`, `point_profile`, `admin_boundary_query`
- 数据角色：`subject`*（block）, `population`（`*` = 必选）
- 路由关键词：地铁站步行、轨道接驳、站点覆盖、metro walk access
- 内容指纹：`92335c69877d732b…`

### `walking_accessibility_gap` — 步行可达盲区

设施步行覆盖盲区识别（覆盖/未覆盖分区 + 缺口 disclosure）：网络口径义务。

- 任务族：`accessibility_analysis`
- 主制图：`proximity_overlay`；辅：`administrative_choropleth`
- 核心能力：`poi_query`, `service_area`, `admin_boundary_query`, `spatial_join`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 科学义务：`gap_network_semantics`（disclosure → GAP_NETWORK_SEMANTICS）
- 路由关键词：盲区、覆盖缺口、未覆盖、服务空白、coverage gap、underserved area、blind zone
- 内容指纹：`e866e547cd79b702…`

## change_detection

### `bitemporal_raster_change` — 两期栅格变化检测

两期栅格差值/分类后对比变化：配准义务 + 变化阈值披露。

- 任务族：`change_detection`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `raster_change_detection`, `zonal_statistics`
- 数据角色：`baseline`*, `target_time`*（`*` = 必选）
- 科学义务：`change_dual_date_required`（temporal → CHANGE_DUAL_DATE_REQUIRED）；`change_registration`（transformation → CHANGE_REGISTRATION_REQUIRED）
- 语义回退：`CHANGE_DUAL_DATE_REQUIRED` → single_date_view（degraded）
- 路由关键词：两期对比、变化检测、前后对比、change detection、two date comparison
- 内容指纹：`95d0aeaa636e8e34…`

### `change_area_accounting` — 变化面积台账

变化图斑面积台账（新增/减少面积 + 行政区归集）：面积口径披露。

- 任务族：`change_detection`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`raster_surface`
- 核心能力：`raster_source`, `raster_change_detection`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`baseline`*, `target_time`*, `boundary`*（block）（`*` = 必选）
- 科学义务：`change_dual_date_required`（temporal → CHANGE_DUAL_DATE_REQUIRED）
- 语义回退：`CHANGE_DUAL_DATE_REQUIRED` → single_date_view（degraded）
- 路由关键词：变化面积、新增面积、减少面积、面积台账、change area accounting
- 内容指纹：`ca410358d98c6687…`

### `landcover_change_inventory` — 地类变化台账

两期地类转移台账（转移矩阵 + 变化图斑）：分类口径一致性义务。

- 任务族：`change_detection`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`raster_surface`
- 核心能力：`raster_source`, `raster_change_detection`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`baseline`*, `target_time`*, `boundary`*（block）（`*` = 必选）
- 科学义务：`change_dual_date_required`（temporal → CHANGE_DUAL_DATE_REQUIRED）；`change_class_scheme_consistent`（disclosure → CHANGE_CLASS_SCHEME_CONSISTENCY）
- 语义回退：`CHANGE_DUAL_DATE_REQUIRED` → single_date_view（degraded）
- 路由关键词：地类变化、转移矩阵、土地利用变化、占地变化、land cover change、change accounting
- 内容指纹：`bd74790d9d661bb5…`

### `ndvi_change_trend` — 植被变化趋势

植被指数多期变化趋势（退化/恢复方向判断）：趋势期数义务。

- 任务族：`change_detection`, `temporal_trend`, `vegetation_index`
- 主制图：`raster_surface`；辅：`administrative_aggregation`
- 核心能力：`raster_source`, `ndvi`, `spectral_index`, `temporal_trend`, `raster_change_detection`, `zonal_statistics`
- 数据角色：`baseline`*, `target_time`*（`*` = 必选）
- 科学义务：`veg_trend_min_observations`（precondition → VEG_TREND_INSUFFICIENT_OBSERVATIONS）
- 语义回退：`VEG_TREND_INSUFFICIENT_OBSERVATIONS` → bitemporal_difference（degraded）
- 路由关键词：植被退化、绿化变化、植被恢复、生态变化、vegetation degradation、ndvi change
- 内容指纹：`4f03f6e0b54c1746…`

### `poi_temporal_comparison` — POI 两期对比

POI/设施两期数量对比（新增/消失点位）：数据源时点一致性义务。

- 任务族：`change_detection`, `administrative_statistic`
- 主制图：`point_overlay`；辅：`administrative_choropleth`
- 核心能力：`poi_query`, `temporal_profile`, `admin_boundary_query`, `admin_aggregation`, `spatial_join`
- 数据角色：`subject`*（block）, `comparison_time`*（`*` = 必选）
- 科学义务：`poi_snapshot_time_consistent`（disclosure → POI_SNAPSHOT_TIME_CONSISTENCY）
- 语义回退：`DATA_ROLE_MISSING_COMPARISON_TIME` → current_view（degraded）
- 路由关键词：新增了、比去年、两期点位、点位变化、new venues compared to
- 内容指纹：`d2c64c8ee5c82af2…`

### `urban_expansion_monitor` — 城市扩张监测

建成区/城镇扩张监测（两期建成区范围对比 + 行政区扩张统计）。

- 任务族：`change_detection`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`raster_surface`
- 核心能力：`raster_source`, `raster_change_detection`, `band_math`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`baseline`*, `target_time`*, `boundary`*（block）（`*` = 必选）
- 科学义务：`change_dual_date_required`（temporal → CHANGE_DUAL_DATE_REQUIRED）
- 语义回退：`CHANGE_DUAL_DATE_REQUIRED` → single_date_view（degraded）
- 路由关键词：城市扩张、建成区扩张、城镇扩展、扩张监测、urban expansion、urban growth
- 内容指纹：`64fc6203496d33ea…`

## density

### `density_grid_fishnet` — 渔网格网密度

规则渔网格网聚合密度：等面积单元计数 choropleth，适合与行政边界叠加。

- 任务族：`distribution_overview`, `analytical_density`
- 主制图：`aggregate_grid`；辅：`administrative_aggregation`
- 核心能力：`poi_query`, `grid_binning`, `admin_boundary_query`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 语义回退：`INSUFFICIENT_POINTS` → point_distribution（degraded）
- 路由关键词：渔网、网格密度、方格统计、fishnet grid、grid aggregation
- 内容指纹：`bbae2f7e000572bd…`

### `density_grid_h3` — H3 六边形格网密度

H3 六边形格网聚合密度：格网计数 choropleth（聚合表达，非核密度估计）。

- 任务族：`distribution_overview`, `analytical_density`, `concentration_analysis`
- 主制图：`aggregate_grid`；辅：`point_overlay`
- 核心能力：`poi_query`, `grid_binning`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 语义回退：`INSUFFICIENT_POINTS` → point_distribution（degraded）
- 路由关键词：h3、六边形、蜂窝格网、h3 hexbin、hexagon density
- 内容指纹：`723c43908ba91524…`

### `density_hotspot_screening` — 热点初筛

聚集初筛：先视觉热力/格网聚合锁定候选区，显著性检验交由统计分析工作流（不得越权宣称）。

- 任务族：`concentration_analysis`
- 主制图：`visual_heatmap`；辅：`hotspot_overlay`, `aggregate_grid`
- 核心能力：`poi_query`, `kde_density`, `grid_binning`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`hotspot_significance_requires_test`（disclosure → HOTSPOT_SCREENING_NOT_SIGNIFICANCE）
- 语义回退：`INSUFFICIENT_POINTS` → point_distribution（degraded）
- 路由关键词：热点初筛、聚集筛查、初步热点、hotspot screening、clustering screening
- 内容指纹：`f8377cced17f4f49…`

### `density_kde_surface` — KDE 核密度面

核密度估计连续表面：样本充足性前置（numeric 样本守卫），不足时降级视觉热力并披露。

- 任务族：`concentration_analysis`, `analytical_density`
- 主制图：`visual_heatmap`；辅：`raster_surface`, `point_overlay`
- 核心能力：`poi_query`, `kde_density`, `density_surface`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`kde_sample_sufficiency`（precondition → KDE_INSUFFICIENT_SAMPLES）
- 语义回退：`KDE_INSUFFICIENT_SAMPLES` → visual_heatmap（approximation）
- 路由关键词：核密度、kde、密度面、kde、kernel density
- 内容指纹：`9e919c154fb50215…`

### `density_quantitative_per_area` — 定量密度分析

单位面积定量密度（个/km²）：行政聚合 + 面积分母归一化，分母缺失时禁止密度结论。

- 任务族：`analytical_density`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`point_overlay`
- 核心能力：`admin_boundary_query`, `admin_aggregation`, `rate_aggregation`, `point_profile`
- 数据角色：`subject`*（block）, `boundary`*（block）, `denominator`*（block）（`*` = 必选）
- 科学义务：`density_requires_denominator`（denominator → DENSITY_MISSING_AREA_DENOMINATOR）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → raw_counts（degraded）
- 路由关键词：定量密度、单位面积、密度排名、quantitative density、density per area
- 内容指纹：`949b4bfc00f4848d…`

### `density_visual_overview` — 视觉密度概览

纯视觉密度感知（descriptive）：热力图表达疏密，不附统计显著性声明。

- 任务族：`distribution_overview`, `concentration_analysis`
- 主制图：`visual_heatmap`；辅：`point_overlay`
- 核心能力：`poi_query`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`density_visual_not_significance`（disclosure → DENSITY_VISUAL_NOT_SIGNIFICANCE）
- 语义回退：`INSUFFICIENT_POINTS` → point_distribution（degraded）
- 路由关键词：疏密、密度大概、密集程度、哪里密、visual density、density impression
- 内容指纹：`4e4e0b335799ab74…`

## distribution

### `admin_feature_audit` — 行政区划要素清查

按行政区逐级清查要素数量（各乡镇/街道/村），产出计数台账 + 分级填色。

- 任务族：`administrative_statistic`, `distribution_overview`
- 主制图：`administrative_choropleth`；辅：`point_overlay`
- 核心能力：`poi_query`, `admin_boundary_query`, `admin_aggregation`, `category_breakdown`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 语义回退：`NEEDS_ADMIN_UNITS` → point_distribution（degraded）
- 路由关键词：清查、逐级统计、各乡镇、各街道、各街道数量、feature audit by admin unit
- 内容指纹：`a85cfc190981327a…`

### `cultural_facility_distribution` — 文体设施分布

图书馆/体育馆/博物馆等文体设施分布概览。

- 任务族：`distribution_overview`, `simple_view`
- 主制图：`visual_heatmap`；辅：`point_overlay`
- 核心能力：`poi_query`, `admin_boundary_query`, `point_profile`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 语义回退：`INSUFFICIENT_POINTS` → point_distribution（degraded）
- 路由关键词：文体设施、图书馆、博物馆、体育馆分布、library distribution、museum distribution
- 内容指纹：`6117c4526887b87d…`

### `edu_facility_distribution` — 教育设施分布概览

学校/幼儿园等教育设施的分布概览：视觉热力 + 点叠加 + 行政聚合；数量层面不宣称密度显著性。

- 任务族：`distribution_overview`, `simple_view`
- 主制图：`visual_heatmap`；辅：`point_overlay`, `administrative_aggregation`
- 核心能力：`poi_query`, `admin_boundary_query`, `point_profile`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 语义回退：`INSUFFICIENT_POINTS` → point_distribution（degraded）
- 路由关键词：教育设施、学校分布、幼儿园、中小学分布、school distribution、education facility
- 内容指纹：`9fc858c484164975…`

### `emergency_shelter_distribution` — 应急避难场所分布

避难场所/应急设施分布：点图 + 行政清查，供应急规划引用（覆盖评价另行可达性工作流）。

- 任务族：`distribution_overview`, `simple_view`
- 主制图：`point_overlay`；辅：`administrative_aggregation`
- 核心能力：`poi_query`, `admin_boundary_query`, `admin_aggregation`, `point_profile`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：避难场所、应急避难、人防设施、应急设施分布、emergency shelter、evacuation site
- 内容指纹：`a01e2933c4c0403a…`

### `financial_branch_distribution` — 金融网点分布

银行/ATM 等金融网点分布概览。

- 任务族：`distribution_overview`, `simple_view`
- 主制图：`visual_heatmap`；辅：`point_overlay`
- 核心能力：`poi_query`, `admin_boundary_query`, `point_profile`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 语义回退：`INSUFFICIENT_POINTS` → point_distribution（degraded）
- 路由关键词：银行网点、atm、金融网点、营业部分布、bank branch、atm distribution
- 内容指纹：`04e67a47e4ded466…`

### `green_space_distribution` — 公园绿地分布

公园/绿地分布：面+点混合主体，点入口热力 + 面状绿地的行政区清查。

- 任务族：`distribution_overview`
- 主制图：`point_overlay`；辅：`administrative_aggregation`
- 核心能力：`poi_query`, `admin_boundary_query`, `admin_aggregation`, `point_profile`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：公园、绿地、绿化分布、绿地分布、park distribution、green space
- 内容指纹：`b773b68f4a02b945…`

### `healthcare_facility_distribution` — 医疗设施分布概览

医院/诊所/药店等医疗设施的分布概览：热力 + 点图 + 行政聚合统计。

- 任务族：`distribution_overview`, `simple_view`
- 主制图：`visual_heatmap`；辅：`point_overlay`, `administrative_aggregation`
- 核心能力：`poi_query`, `admin_boundary_query`, `point_profile`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 语义回退：`INSUFFICIENT_POINTS` → point_distribution（degraded）
- 路由关键词：医疗设施、医院分布、诊所分布、药店分布、hospital distribution、healthcare facility
- 内容指纹：`613b87d09a06b7d3…`

### `infrastructure_node_distribution` — 市政节点设施分布

加油站/充电站等市政节点设施分布：点图 + 类别细分。

- 任务族：`distribution_overview`, `categorical_distribution`
- 主制图：`categorical_thematic`；辅：`point_overlay`
- 核心能力：`poi_query`, `admin_boundary_query`, `category_breakdown`, `point_profile`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：加油站、充电站、市政设施、充电桩分布、charging station、gas station distribution
- 内容指纹：`432ae9746f7b0474…`

### `landmark_simple_view` — 地标要素轻量查看

「给我看看 XX」的最小分析路径：主体点图 + 属性画像，禁止过度分析（anti-over-analysis）。

- 任务族：`simple_view`
- 主制图：`simple_point_map`
- 核心能力：`poi_query`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 路由关键词：在地图上标出、地图上显示、map the、display on map
- 内容指纹：`7210fee6c372e9de…`

### `logistics_facility_distribution` — 物流网点分布

物流园区/快递网点等物流设施分布概览。

- 任务族：`distribution_overview`
- 主制图：`visual_heatmap`；辅：`point_overlay`
- 核心能力：`poi_query`, `admin_boundary_query`, `point_profile`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 语义回退：`INSUFFICIENT_POINTS` → point_distribution（degraded）
- 路由关键词：物流园、快递网点、物流网点、仓储分布、logistics facility、warehouse distribution
- 内容指纹：`126af2666ac4bb77…`

### `poi_inventory_catalog` — POI 要素清单

「XX 有哪些 / 要素清单 / 本底调查」：全量清查 + 类别构成 + 行政归属，强调清单完整性而非密度。

- 任务族：`distribution_overview`, `categorical_distribution`
- 主制图：`categorical_thematic`；辅：`point_overlay`
- 核心能力：`poi_query`, `category_breakdown`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：要素清单、本底调查、清查台账、资源台账、inventory、catalog of、asset list
- 内容指纹：`96e8ae659ec78a8d…`

### `transit_station_distribution` — 公交地铁站点分布

轨道交通/公交站点分布：点叠加为主，格网聚合为辅（站点为线网离散采样，密度语义需谨慎）。

- 任务族：`distribution_overview`, `simple_view`
- 主制图：`point_overlay`；辅：`aggregate_grid`
- 核心能力：`poi_query`, `admin_boundary_query`, `point_profile`, `grid_binning`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：地铁站点、公交站、站点分布、轨道站点、metro stations、bus stops、transit stops
- 内容指纹：`e9fa36cf46166ec0…`

## disaster

### `disaster_shelter_accessibility` — 避难场所可达保障

避难场所可达性保障评价（响应圈 + 盲区 + 容量提示）：阈值口径披露。

- 任务族：`accessibility_analysis`, `risk_exposure`, `spatial_equity`
- 主制图：`proximity_overlay`；辅：`administrative_choropleth`
- 核心能力：`poi_query`, `service_area`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）, `population`（`*` = 必选）
- 科学义务：`shelter_time_threshold`（disclosure → SHELTER_TIME_THRESHOLD_DISCLOSURE）
- 路由关键词：避难所可达、疏散保障、避难圈、shelter accessibility
- 内容指纹：`23563821a2d06f3d…`

### `emergency_resource_dispatch` — 应急资源调度分析

应急资源点到需求点分配（最近设施 + 服务区）：网络口径与降级披露。

- 任务族：`network_route`, `risk_exposure`, `accessibility_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `closest_facility`, `service_area`, `point_profile`
- 数据角色：`subject`*（block）, `network`*（`*` = 必选）
- 语义回退：`NETWORK_DISCONNECTED` → euclidean_assignment（proxy）
- 路由关键词：应急调度、救援分配、物资调度、应急资源、emergency dispatch analysis
- 内容指纹：`fdce0d096691f313…`

### `flood_inundation_screen` — 内涝影响筛查

内涝点影响范围筛查（积水点缓冲 + 受体连接）：几何代理披露。

- 任务族：`risk_exposure`, `watershed_analysis`, `proximity_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `geometry_buffer`, `multi_ring_buffer`, `spatial_join`, `category_breakdown`
- 数据角色：`hazard`*（block）, `subject`*（block）（`*` = 必选）
- 科学义务：`inundation_buffer_proxy`（disclosure → FLOOD_GEOMETRIC_APPROXIMATION）
- 路由关键词：内涝点、积水点、易涝点影响、waterlogging screen
- 内容指纹：`11e34fcafadc6619…`

### `geological_hazard_inventory` — 地质灾害点清单

地灾隐患点分布清单（密度 + 行政区归集）：点位台账语义。

- 任务族：`distribution_overview`, `risk_exposure`, `administrative_statistic`
- 主制图：`point_overlay`；辅：`administrative_choropleth`
- 核心能力：`poi_query`, `admin_boundary_query`, `admin_aggregation`, `point_profile`, `kde_density`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 科学义务：`hazard_point_density_semantics`（disclosure → HAZARD_DENSITY_IS_DESCRIPTIVE）
- 路由关键词：地灾点、隐患点、地质灾害清单、隐患排查、geological hazard inventory
- 内容指纹：`1d440edadcdd20e6…`

### `seismic_intensity_exposure_screen` — 震后影响快速筛查

震中缓冲/烈度代理 × 人口设施暴露快速筛查：近似语义强披露（非专业烈度场）。

- 任务族：`risk_exposure`
- 主制图：`proximity_overlay`；辅：`administrative_choropleth`
- 核心能力：`geometry_buffer`, `multi_ring_buffer`, `geometry_overlay`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`hazard`*（block）, `receptor`, `boundary`*（block）（`*` = 必选）
- 科学义务：`seismic_quick_screen_proxy`（disclosure → SEISMIC_PROXIMITY_PROXY）
- 路由关键词：地震影响、震后筛查、极重灾区估计、earthquake quick screening
- 内容指纹：`d6169247a2050943…`

## environment

### `air_quality_admin_stats` — 行政区空气质量统计

行政区空气质量站点读数聚合统计（读数 ≠ 空间面）：站点口径披露。

- 任务族：`administrative_statistic`, `temporal_trend`
- 主制图：`administrative_choropleth`；辅：`point_overlay`
- 核心能力：`poi_query`, `admin_boundary_query`, `admin_aggregation`, `temporal_profile`, `temporal_aggregate`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 科学义务：`aqi_point_semantics`（disclosure → AQI_POINT_SAMPLING_SEMANTICS）
- 路由关键词：各区空气质量、空气质量排名、aqi统计、air quality by district
- 内容指纹：`8d0ddd3e12f04aaf…`

### `air_quality_surface` — 空气质量插值面

空气质量（PM2.5/PM10/AQI）站点插值：数值字段 + 时间切片义务，超标阈值分级。

- 任务族：`raster_distribution`
- 主制图：`raster_surface`；辅：`administrative_aggregation`
- 核心能力：`poi_query`, `spatial_interpolation`, `point_profile`, `zonal_statistics`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`aqi_min_samples`（precondition → AQI_INSUFFICIENT_STATIONS）
- 语义回退：`AQI_INSUFFICIENT_STATIONS` → station_points（degraded）
- 路由关键词：空气质量、pm2.5、pm10、aqi、污染分布、air quality、pm25 surface、aqi map
- 内容指纹：`7777587ec05736da…`

### `env_sensitivity_zoning` — 环境敏感区划

环境敏感性分区（水源/生态/敏感点约束叠加）：约束口径披露。

- 任务族：`suitability_assessment`, `risk_exposure`
- 主制图：`raster_surface`；辅：`proximity_overlay`
- 核心能力：`raster_source`, `raster_reclassify`, `geometry_overlay`, `geometry_buffer`, `zonal_statistics`
- 数据角色：`subject`*（block）, `constraint`（`*` = 必选）
- 路由关键词：环境敏感区、生态敏感、环境管控单元、environmental sensitivity zoning
- 内容指纹：`b9535a3b2206e609…`

### `monitoring_station_coverage` — 监测站点覆盖

环境监测站覆盖评价（站网密度 + 空白区）：站网密度语义（数量统计 ≠ 插值精度）。

- 任务族：`distribution_overview`, `administrative_statistic`, `accessibility_analysis`
- 主制图：`point_overlay`；辅：`administrative_choropleth`
- 核心能力：`poi_query`, `admin_boundary_query`, `admin_aggregation`, `point_profile`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：监测站、站点覆盖、站网、监测点位、monitoring station coverage
- 内容指纹：`c58ba521cfea957d…`

### `noise_buffer_screen` — 噪声影响筛查

噪声源（交通/工业）缓冲筛查：几何缓冲代理语义（非声场模拟）。

- 任务族：`proximity_analysis`, `risk_exposure`
- 主制图：`proximity_overlay`；辅：`categorical_thematic`
- 核心能力：`poi_query`, `geometry_buffer`, `multi_ring_buffer`, `spatial_join`, `category_breakdown`
- 数据角色：`hazard`*（block）, `subject`*（block）（`*` = 必选）
- 科学义务：`noise_proxy_disclosure`（disclosure → NOISE_BUFFER_IS_PROXY）
- 路由关键词：噪声、噪音影响、声环境、沿线噪声、noise exposure screening
- 内容指纹：`ca3d71690f0d869d…`

### `pollution_source_buffer_screen` — 污染源缓冲筛查

污染源周边敏感目标缓冲筛查（多环缓冲 + 受体连接）：距离口径披露。

- 任务族：`proximity_analysis`, `risk_exposure`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `multi_ring_buffer`, `geometry_buffer`, `spatial_join`, `category_breakdown`
- 数据角色：`hazard`*（block）, `subject`*（block）（`*` = 必选）
- 科学义务：`pollution_buffer_semantics`（disclosure → BUFFER_DISTANCE_SEMANTICS）
- 路由关键词：污染源、卫生防护距离、周边敏感点、环境防护、pollution source buffer
- 内容指纹：`b48098fe5045aef3…`

## equity

### `education_equity_per_capita` — 教育资源人均公平性

教育设施人均配置公平性（行政区人均学位/学校数）：分母义务强制。

- 任务族：`spatial_equity`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`point_overlay`
- 核心能力：`poi_query`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`, `category_breakdown`
- 数据角色：`subject`*（block）, `boundary`*（block）, `denominator`*（block）（`*` = 必选）
- 科学义务：`equity_denominator_required`（denominator → EQUITY_MISSING_DENOMINATOR）；`equity_measure_semantics`（disclosure → EQUITY_MEASURE_SEMANTICS）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → count_density_view（degraded）
- 路由关键词：教育资源、学位公平、学校均衡、教育公平、education equity、school balance
- 内容指纹：`52cbaeb44ea552c1…`

### `emergency_equity_coverage` — 应急服务均衡覆盖

应急设施响应覆盖均衡性（行政区覆盖比离散）：阈值口径 + 分母义务。

- 任务族：`spatial_equity`, `accessibility_analysis`
- 主制图：`administrative_choropleth`；辅：`proximity_overlay`
- 核心能力：`poi_query`, `service_area`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）, `denominator`*（block）（`*` = 必选）
- 科学义务：`equity_denominator_required`（denominator → EQUITY_MISSING_DENOMINATOR）；`equity_measure_semantics`（disclosure → EQUITY_MEASURE_SEMANTICS）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → count_density_view（degraded）
- 路由关键词：应急均衡、消防均衡、避难公平、emergency equity
- 内容指纹：`6baa22a0ab12d11a…`

### `facility_per_capita_profile` — 设施人均画像

行政区设施人均拥有量画像（人均台账）：分母义务，无分母只报总量。

- 任务族：`spatial_equity`, `administrative_statistic`, `analytical_density`
- 主制图：`administrative_choropleth`
- 核心能力：`poi_query`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`, `point_profile`
- 数据角色：`subject`*（block）, `boundary`*（block）, `denominator`*（block）（`*` = 必选）
- 科学义务：`per_capita_denominator_required`（denominator → PER_CAPITA_DENOMINATOR_REQUIRED）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → count_density_view（degraded）
- 路由关键词：人均拥有、万人拥有、千人指标、人均配置、per capita facilities
- 内容指纹：`892f600229d2bfe8…`

### `healthcare_equity_access` — 医疗可达公平性

医疗服务可达性公平（行政区可达值离散度）：可达性口径与分母义务。

- 任务族：`spatial_equity`, `accessibility_analysis`
- 主制图：`administrative_choropleth`；辅：`proximity_overlay`
- 核心能力：`poi_query`, `service_area`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`, `global_morans_i`
- 数据角色：`subject`*（block）, `boundary`*（block）, `denominator`*（block）（`*` = 必选）
- 科学义务：`equity_denominator_required`（denominator → EQUITY_MISSING_DENOMINATOR）；`equity_measure_semantics`（disclosure → EQUITY_MEASURE_SEMANTICS）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → count_density_view（degraded）
- 路由关键词：医疗公平、就医公平、医疗均衡、医疗资源分布合理性、healthcare equity、medical accessibility fairness
- 内容指纹：`22d2ba3f50fbc5ab…`

### `park_access_equity` — 公园绿地可达公平

公园绿地步行可达公平性（覆盖人口比例）：分母义务。

- 任务族：`spatial_equity`, `accessibility_analysis`
- 主制图：`administrative_choropleth`；辅：`proximity_overlay`
- 核心能力：`poi_query`, `service_area`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）, `denominator`*（block）（`*` = 必选）
- 科学义务：`equity_denominator_required`（denominator → EQUITY_MISSING_DENOMINATOR）；`equity_measure_semantics`（disclosure → EQUITY_MEASURE_SEMANTICS）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → count_density_view（degraded）
- 路由关键词：公园公平、绿地公平、公园可达均衡、park equity、green space fairness
- 内容指纹：`ee09026d7f5f16e4…`

## exposure

### `facility_exposure_inventory` — 设施暴露清单

影响区内设施/承灾体清单（数量与类别构成）：清单语义，非损失评估。

- 任务族：`risk_exposure`, `distribution_overview`
- 主制图：`proximity_overlay`；辅：`categorical_thematic`
- 核心能力：`geometry_buffer`, `poi_query`, `geometry_overlay`, `spatial_join`, `category_breakdown`
- 数据角色：`hazard`*（block）, `subject`*（block）（`*` = 必选）
- 科学义务：`exposure_not_loss`（disclosure → EXPOSURE_NOT_LOSS_ESTIMATE）
- 路由关键词：影响区内设施、暴露设施、受影响设施、exposed facilities
- 内容指纹：`386e8d391158af19…`

### `hazard_buffer_receptor_screen` — 危险源缓冲受体筛查

危险源周边缓冲带内受体筛查（多环缓冲 + 空间连接）：缓冲距离口径披露。

- 任务族：`risk_exposure`, `proximity_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`multi_ring_buffer`, `geometry_buffer`, `poi_query`, `spatial_join`, `category_breakdown`
- 数据角色：`hazard`*（block）, `subject`*（block）（`*` = 必选）
- 科学义务：`buffer_distance_semantics`（disclosure → BUFFER_DISTANCE_SEMANTICS）
- 路由关键词：安全距离、卫生防护距离、周边筛查、缓冲区受体、buffer receptor screening、safety distance
- 内容指纹：`96aa985ca1ce5558…`

### `population_exposure_estimate` — 人口暴露估算

影响区 × 人口暴露估算（影响区内人口规模）：人口分母义务。

- 任务族：`risk_exposure`, `spatial_equity`
- 主制图：`administrative_choropleth`；辅：`proximity_overlay`
- 核心能力：`geometry_buffer`, `geometry_overlay`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`hazard`*（block）, `denominator`*（block）, `boundary`*（block）（`*` = 必选）
- 科学义务：`exposure_population_required`（denominator → EXPOSURE_POPULATION_MISSING）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → exposure_area_map（degraded）
- 路由关键词：人口暴露、影响人口、受影响人数、population exposure、affected population
- 内容指纹：`5aa1003e00bb8069…`

### `vulnerability_profile_overlay` — 易损性画像叠加

敏感群体/易损属性与影响区叠加（脆弱性画像）：代理指标语义披露。

- 任务族：`risk_exposure`, `spatial_equity`
- 主制图：`administrative_choropleth`；辅：`proximity_overlay`
- 核心能力：`geometry_buffer`, `geometry_overlay`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`hazard`*（block）, `receptor`, `boundary`*（block）, `denominator`*（block）（`*` = 必选）
- 科学义务：`vulnerability_proxy_disclosure`（disclosure → VULNERABILITY_PROXY_SEMANTICS）
- 路由关键词：易损性、脆弱群体、敏感区域画像、vulnerability profile
- 内容指纹：`ae63772cb0de02ee…`

## hydrology

### `basin_pour_point_stats` — 出口断面流域统计

指定出口点（pour point）的流域圈定与属性统计（面积/坡度/土地利用构成）。

- 任务族：`watershed_analysis`, `administrative_statistic`
- 主制图：`proximity_overlay`；辅：`administrative_aggregation`
- 核心能力：`raster_source`, `terrain_hydrology`, `point_profile`, `zonal_statistics`, `admin_boundary_query`
- 数据角色：`elevation`*（block）, `reference`*（block）（`*` = 必选）
- 科学义务：`hydro_dem_band_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`hydro_dem_conditioned`（transformation → HYDRO_DEM_CONDITIONING_REQUIRED）；`hydro_metric_crs`（precondition → HYDRO_METRIC_CRS_REQUIRED）
- 语义回退：`HYDRO_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：出口断面、水文站流域、控制断面、pour point、pour point basin
- 内容指纹：`79e2bdaf3003324f…`

### `drainage_density_stats` — 排水分区密度统计

分区排水密度（河网长度/面积）：面积分母义务，缺分母不得称密度。

- 任务族：`watershed_analysis`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`proximity_overlay`
- 核心能力：`raster_source`, `terrain_hydrology`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`elevation`*（block）, `boundary`*（block）（`*` = 必选）
- 科学义务：`hydro_dem_band_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`hydro_dem_conditioned`（transformation → HYDRO_DEM_CONDITIONING_REQUIRED）；`hydro_metric_crs`（precondition → HYDRO_METRIC_CRS_REQUIRED）；`drainage_density_denominator`（denominator → DRAINAGE_DENSITY_MISSING_AREA）
- 语义回退：`HYDRO_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）; `DATA_ROLE_MISSING_DENOMINATOR` → stream_length_stats（degraded）
- 路由关键词：排水密度、河网密度、水系密度、drainage density
- 内容指纹：`a010df058f0d4f54…`

### `flood_extent_screening` — 淹没范围初筛

基于 DEM 的水位高程淹没初筛（平推法/盆地填充近似）：必须披露为几何近似，非水动力模拟。

- 任务族：`watershed_analysis`, `risk_exposure`, `terrain_analysis`
- 主制图：`raster_surface`；辅：`proximity_overlay`
- 核心能力：`raster_source`, `terrain_hydrology`, `terrain_derivatives`, `raster_reclassify`
- 数据角色：`elevation`*（block）（`*` = 必选）
- 科学义务：`hydro_dem_band_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`hydro_dem_conditioned`（transformation → HYDRO_DEM_CONDITIONING_REQUIRED）；`hydro_metric_crs`（precondition → HYDRO_METRIC_CRS_REQUIRED）；`flood_not_hydrodynamic`（disclosure → FLOOD_GEOMETRIC_APPROXIMATION）
- 语义回退：`HYDRO_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：淹没范围、洪水淹没、内涝初筛、水位推演、flood extent、inundation screening
- 内容指纹：`d375192d41b39b66…`

### `flow_accumulation_mapping` — 汇流累积制图

汇流累积量面图：河道潜在路径刻画（单元数披露，非实测水系）。

- 任务族：`watershed_analysis`, `terrain_analysis`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `terrain_hydrology`, `terrain_derivatives`
- 数据角色：`elevation`*（block）（`*` = 必选）
- 科学义务：`hydro_dem_band_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`hydro_dem_conditioned`（transformation → HYDRO_DEM_CONDITIONING_REQUIRED）；`hydro_metric_crs`（precondition → HYDRO_METRIC_CRS_REQUIRED）
- 语义回退：`HYDRO_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：汇流累积、流累积量、径流路径、flow accumulation
- 内容指纹：`1c86a539de1c0f8b…`

### `stream_network_extraction` — 河网提取

由汇流累积阈值提取河网（栅格→矢量线）：阈值敏感性披露。

- 任务族：`watershed_analysis`
- 主制图：`proximity_overlay`；辅：`raster_surface`
- 核心能力：`raster_source`, `terrain_hydrology`, `terrain_derivatives`
- 数据角色：`elevation`*（block）（`*` = 必选）
- 科学义务：`hydro_dem_band_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`hydro_dem_conditioned`（transformation → HYDRO_DEM_CONDITIONING_REQUIRED）；`hydro_metric_crs`（precondition → HYDRO_METRIC_CRS_REQUIRED）；`stream_threshold_disclosure`（disclosure → STREAM_THRESHOLD_SENSITIVITY）
- 语义回退：`HYDRO_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：河网提取、水系提取、河道提取、stream extraction、river network
- 内容指纹：`b065f37674b737f7…`

### `watershed_delineation_workflow` — 流域划分

DEM 流域划分：填洼 → 流向 → 汇流累积 → 出口点流域提取； Conditioning 义务强制。

- 任务族：`watershed_analysis`, `terrain_analysis`
- 主制图：`raster_surface`；辅：`proximity_overlay`
- 核心能力：`raster_source`, `terrain_hydrology`, `terrain_derivatives`, `zonal_statistics`
- 数据角色：`elevation`*（block）（`*` = 必选）
- 科学义务：`hydro_dem_band_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`hydro_dem_conditioned`（transformation → HYDRO_DEM_CONDITIONING_REQUIRED）；`hydro_metric_crs`（precondition → HYDRO_METRIC_CRS_REQUIRED）
- 语义回退：`HYDRO_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：流域划分、集水区、分水岭、汇水区、watershed delineation、catchment
- 内容指纹：`14702aaf464c54ad…`

## interpolation

### `idw_interpolation_workflow` — IDW 反距离加权插值

IDW 插值连续表面：方法简单透明，样本充足性义务 + 平滑参数披露。

- 任务族：`raster_distribution`
- 主制图：`raster_surface`；辅：`point_overlay`
- 核心能力：`poi_query`, `spatial_interpolation`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`idw_numeric_field`（precondition → IDW_NUMERIC_FIELD_REQUIRED）；`idw_min_samples`（precondition → IDW_INSUFFICIENT_SAMPLES）
- 语义回退：`IDW_INSUFFICIENT_SAMPLES` → point_map（degraded）
- 路由关键词：idw、反距离、距离加权、idw、inverse distance weighting
- 内容指纹：`e55fb3a08d87c447…`

### `interpolation_cv_compare` — 插值方法交叉验证比较

多候选插值方法 CV 比较（克里金/IDW/样条）：以验证误差选方法，禁止静默换方法。

- 任务族：`raster_distribution`
- 主制图：`raster_surface`；辅：`point_overlay`
- 核心能力：`poi_query`, `spatial_interpolation`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`cv_numeric_field`（precondition → CV_NUMERIC_FIELD_REQUIRED）；`cv_min_samples`（precondition → CV_INSUFFICIENT_SAMPLES）
- 语义回退：`CV_INSUFFICIENT_SAMPLES` → single_method_default（degraded）
- 路由关键词：插值比较、交叉验证、哪个插值方法、cv、interpolation cross validation、method comparison
- 内容指纹：`c901aa3b078d5acb…`

### `interpolation_uncertainty_map` — 插值不确定性图

插值方差/不确定性伴随图层：克里金方差面 + 采样点置信标注，uncertainty 维强制。

- 任务族：`raster_distribution`
- 主制图：`raster_surface`；辅：`point_overlay`
- 核心能力：`poi_query`, `spatial_interpolation`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`uncertainty_requires_variance_output`（uncertainty → INTERPOLATION_VARIANCE_UNAVAILABLE）
- 路由关键词：插值不确定性、方差面、精度图、interpolation uncertainty、variance surface
- 内容指纹：`ba96745ab6da7157…`

### `kriging_interpolation_workflow` — 克里金插值面

克里金插值连续表面：数值字段 + 投影 CRS + 样本充足性前置，CV 不合格不得出面。

- 任务族：`raster_distribution`, `concentration_analysis`
- 主制图：`raster_surface`；辅：`point_overlay`
- 核心能力：`poi_query`, `spatial_interpolation`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`kriging_numeric_field`（precondition → KRIGING_NUMERIC_FIELD_REQUIRED）；`kriging_projected_crs`（precondition → KRIGING_PROJECTED_CRS_REQUIRED）；`kriging_min_samples`（precondition → KRIGING_INSUFFICIENT_SAMPLES）
- 语义回退：`KRIGING_INSUFFICIENT_SAMPLES` → point_map（degraded）; `KRIGING_PROJECTED_CRS_REQUIRED` → point_map（degraded）
- 路由关键词：克里金、kriging、金插值、kriging
- 内容指纹：`526bcc75a7f558d6…`

### `station_field_interpolation` — 站点观测场插值

气象/环境站点观测插值成面（气温/降水/空气质量）：站点密度前置 + 时相对齐义务。

- 任务族：`raster_distribution`, `temporal_trend`
- 主制图：`raster_surface`；辅：`point_overlay`
- 核心能力：`poi_query`, `spatial_interpolation`, `temporal_profile`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`station_min_samples`（precondition → STATION_INSUFFICIENT_SAMPLES）；`station_timeslice_alignment`（disclosure → STATION_TIMESLICE_UNALIGNED）
- 语义回退：`STATION_INSUFFICIENT_SAMPLES` → station_points（degraded）
- 路由关键词：站点插值、气温分布、降水分布、监测站插值、station interpolation、weather surface
- 内容指纹：`254b4b670d08507b…`

## natural_resources

### `cultivated_land_distribution` — 耕地分布清查

耕地分布与面积清查（地类图斑/分类栅格 + 行政区台账）：地类口径披露。

- 任务族：`distribution_overview`, `administrative_statistic`, `raster_distribution`
- 主制图：`administrative_choropleth`；辅：`raster_surface`
- 核心能力：`raster_source`, `raster_reclassify`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`, `category_breakdown`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 科学义务：`landclass_semantics`（disclosure → LANDCLASS_SEMANTICS）
- 路由关键词：耕地、基本农田、耕地分布、耕地面积、cultivated land、farmland distribution
- 内容指纹：`23883ed27e01841e…`

### `forest_orchard_inventory` — 林园地资源清查

林地/园地分布与面积清查（指数辅助 + 行政区台账）。

- 任务族：`distribution_overview`, `administrative_statistic`, `vegetation_index`
- 主制图：`administrative_choropleth`；辅：`raster_surface`
- 核心能力：`raster_source`, `ndvi`, `spectral_index`, `raster_reclassify`, `zonal_statistics`, `admin_boundary_query`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：林地、园地、森林分布、果园分布、forest inventory、orchard distribution
- 内容指纹：`c33d4d619de485cb…`

### `grassland_condition_index` — 草地状况评价

草地覆盖状况/退化趋势评价（植被指数代理）：指数代理语义披露。

- 任务族：`vegetation_index`, `temporal_trend`
- 主制图：`raster_surface`；辅：`administrative_choropleth`
- 核心能力：`raster_source`, `ndvi`, `spectral_index`, `temporal_trend`, `zonal_statistics`, `admin_boundary_query`
- 数据角色：`subject`*（block）, `boundary`（`*` = 必选）
- 科学义务：`grass_ndvi_proxy`（disclosure → VEGETATION_INDEX_PROXY_SEMANTICS）
- 路由关键词：草地、草场状况、草原退化、grassland condition、rangeland
- 内容指纹：`d7d6c63f1473e081…`

### `landcover_area_accounting` — 地类面积台账

地类面积台账（行政区 × 地类面积/占比矩阵）：面积口径与分辨率披露。

- 任务族：`administrative_statistic`, `raster_distribution`
- 主制图：`administrative_choropleth`；辅：`categorical_thematic`
- 核心能力：`raster_source`, `raster_reclassify`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：面积台账、地类面积、国土台账、面积统计、area accounting、land area stats
- 内容指纹：`ae2b1b69fab85a78…`

### `resource_change_detection` — 资源变化监测

自然资源两期变化监测（占用/恢复图斑 + 台账）：配准与口径义务。

- 任务族：`change_detection`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`raster_surface`
- 核心能力：`raster_source`, `raster_change_detection`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`, `category_breakdown`
- 数据角色：`baseline`*, `target_time`*, `boundary`*（block）（`*` = 必选）
- 科学义务：`resource_change_dual_date`（temporal → CHANGE_DUAL_DATE_REQUIRED）；`resource_change_registration`（transformation → CHANGE_REGISTRATION_REQUIRED）
- 语义回退：`CHANGE_DUAL_DATE_REQUIRED` → single_date_view（degraded）
- 路由关键词：耕地占用、资源变化、林地变化、占补平衡、land resource change
- 内容指纹：`c74c0f01fbe8f9de…`

### `water_body_inventory` — 水域资源清查

河湖水域分布与面积清查（水体指数/地类）：时相水位语义披露。

- 任务族：`distribution_overview`, `vegetation_index`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`raster_surface`
- 核心能力：`raster_source`, `spectral_index`, `raster_reclassify`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 科学义务：`water_season_semantics`（disclosure → WATER_SEASON_SEMANTICS）
- 路由关键词：水域、湖泊分布、河流水面、湿地分布、water body inventory、lake distribution
- 内容指纹：`03f33fa42642a085…`

## network

### `closest_facility_assignment` — 最近设施分配

需求点到最近设施的路网分配（医院/消防等）：断网降级欧氏最近并披露。

- 任务族：`network_route`, `accessibility_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `closest_facility`, `point_profile`
- 数据角色：`subject`*（block）, `network`*（`*` = 必选）
- 语义回退：`NETWORK_DISCONNECTED` → euclidean_assignment（proxy）
- 路由关键词：最近的医院、最近设施、就近分配、closest facility、nearest allocation
- 内容指纹：`b4d0510eb8bcf2d4…`

### `od_corridor_mapping` — OD 走廊图

OD 流向走廊可视化（弧线/线宽分级）：流量字段义务。

- 任务族：`mobility_flow`
- 主制图：`flow_od_arc`；辅：`point_overlay`
- 核心能力：`poi_query`, `od_flow_mapping`, `od_matrix`, `point_profile`
- 数据角色：`subject`*（block）, `measure`*（`*` = 必选）
- 路由关键词：通勤走廊、流向图、流量分布、od线、od flows、commuting corridors
- 内容指纹：`3ace95542f2be9d1…`

### `od_matrix_analysis` — OD 矩阵分析

起讫点（OD）矩阵计算与强度分级：网络角色 + 断网降级欧氏披露。

- 任务族：`mobility_flow`, `network_route`
- 主制图：`flow_od_arc`；辅：`point_overlay`
- 核心能力：`poi_query`, `od_matrix`, `point_profile`
- 数据角色：`subject`*（block）, `network`*（`*` = 必选）
- 语义回退：`NETWORK_DISCONNECTED` → euclidean_od_matrix（proxy）
- 路由关键词：od矩阵、起讫点、通勤成本矩阵、od matrix、origin destination
- 内容指纹：`24a90dfb552e1df5…`

### `route_optimization_tour` — 多点配送路径优化

多点访问顺序优化（TSP 式配送/巡查路线）：站点数守卫与近似性披露。

- 任务族：`network_route`, `site_selection`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `route_optimization`, `point_profile`
- 数据角色：`subject`*（block）, `network`*（`*` = 必选）
- 科学义务：`tour_approximation_disclosure`（disclosure → TOUR_APPROXIMATION_DISCLOSURE）
- 路由关键词：配送路线、多点巡查、拜访顺序、tsp、route optimization、delivery tour
- 内容指纹：`470af0a39afd7adf…`

### `shortest_path_routing` — 最短路径规划

起终点最短路径（路网）：断网降级欧氏代理必须披露 unreachable 与 snap 质量。

- 任务族：`network_route`, `proximity_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `shortest_path`, `point_profile`
- 数据角色：`subject`*（block）, `network`*（`*` = 必选）
- 科学义务：`route_snap_quality`（disclosure → ROUTE_SNAP_QUALITY）
- 语义回退：`NETWORK_DISCONNECTED` → euclidean_line（proxy）
- 路由关键词：最短路径、最短路线、路径规划、路线规划、怎么走、导航、shortest path、route planning
- 内容指纹：`5f3de48851c6f441…`

### `traffic_status_overview` — 路况状态概览

实时/时段路况等级可视化：数据时点语义强披露（非通行能力分析）。

- 任务族：`network_route`, `simple_view`, `mobility_flow`
- 主制图：`categorical_thematic`；辅：`point_overlay`
- 核心能力：`traffic_status`, `poi_query`, `point_profile`
- 数据角色：`network`*（`*` = 必选）
- 科学义务：`traffic_time_semantics`（disclosure → TRAFFIC_TIME_SEMANTICS）
- 路由关键词：路况、拥堵、实时交通、traffic status、congestion
- 内容指纹：`f6294a9ab4ac7c7e…`

### `transit_accessibility_workflow` — 公交可达分析

公交/轨道线路可达性（站点覆盖+换乘）：时刻表口径披露。

- 任务族：`accessibility_analysis`, `network_route`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `transit_routing`, `service_area`, `point_profile`
- 数据角色：`subject`*（block）, `network`*（`*` = 必选）
- 科学义务：`transit_schedule_semantics`（disclosure → TRANSIT_SCHEDULE_SEMANTICS）
- 路由关键词：公交可达、地铁通达、换乘便利、公共交通、transit accessibility、public transport
- 内容指纹：`4932a7bf81806db2…`

## point_pattern

### `convex_hull_extent` — 分布范围圈定

凸包圈定主体分布范围（活动范围/服务足迹的描述性边界）。

- 任务族：`distribution_overview`, `proximity_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `convex_hull`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 语义回退：`HULL_INSUFFICIENT_POINTS` → point_distribution（degraded）
- 路由关键词：分布范围、活动范围、覆盖圈、足迹、convex hull、activity range
- 内容指纹：`6dcace2fb7a1bc22…`

### `point_pattern_distance` — 最近邻距离格局

最近邻指数（NNI）式点格局刻画：平均最近邻距离与随机模式对比。

- 任务族：`concentration_analysis`
- 主制图：`point_overlay`；辅：`hotspot_overlay`
- 核心能力：`poi_query`, `point_pattern_analysis`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`nni_projected_crs`（precondition → NNI_PROJECTED_CRS_REQUIRED）
- 语义回退：`NNI_PROJECTED_CRS_REQUIRED` → visual_density（degraded）
- 路由关键词：最近邻、点间距离、nni、nearest neighbor index、point distance
- 内容指纹：`6a75e86e31fae9c5…`

### `point_pattern_quadrat` — 点格局检验（象限法）

象限计数点格局检验：均匀/随机/聚集的统计判断，样本量义务前置。

- 任务族：`concentration_analysis`, `spatial_autocorrelation`
- 主制图：`hotspot_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `point_pattern_analysis`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`quadrat_min_points`（precondition → QUADRAT_INSUFFICIENT_POINTS）
- 语义回退：`QUADRAT_INSUFFICIENT_POINTS` → visual_density（degraded）
- 路由关键词：点格局、象限分析、分布检验、point pattern、quadrat analysis
- 内容指纹：`ef6af6bdef879a85…`

### `spatiotemporal_emergence_tracking` — 事件时空涌现追踪

带时序的事件点时空聚类追踪（如投诉/案件涌现）：时间字段义务。

- 任务族：`concentration_analysis`, `temporal_trend`
- 主制图：`hotspot_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `spatiotemporal_clustering`, `temporal_profile`, `point_profile`
- 数据角色：`subject`*（block）, `target_time`*（`*` = 必选）
- 科学义务：`emergence_temporal_field`（temporal → EMERGENCE_TEMPORAL_FIELD_REQUIRED）
- 语义回退：`DATA_ROLE_MISSING_TARGET_TIME` → visual_density（degraded）
- 路由关键词：事件涌现、投诉聚集、案件时空、涌现追踪、event emergence、spatiotemporal events
- 内容指纹：`f6e8c2ffeb104447…`

### `voronoi_service_coverage` — Voronoi 服务域划分

以设施点为种子的 Voronoi 服务域划分：最近设施假设下的覆盖形态（非路网可达）。

- 任务族：`accessibility_analysis`, `proximity_analysis`
- 主制图：`proximity_overlay`；辅：`categorical_thematic`
- 核心能力：`poi_query`, `voronoi_tessellation`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`voronoi_euclidean_disclosure`（disclosure → VORONOI_EUCLIDIAN_PROXY）
- 语义回退：`VORONOI_INSUFFICIENT_SEEDS` → point_distribution（degraded）
- 路由关键词：voronoi、泰森多边形、服务域、势力范围、voronoi、thiessen polygon
- 内容指纹：`2b4ea3744e74ccdb…`

## public_health

### `clinic_coverage_analysis` — 基层医疗覆盖分析

基层医疗机构（社区卫生服务中心/诊所）服务覆盖分析：服务圈口径披露。

- 任务族：`accessibility_analysis`, `distribution_overview`
- 主制图：`proximity_overlay`；辅：`administrative_choropleth`
- 核心能力：`poi_query`, `service_area`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）, `population`（`*` = 必选）
- 科学义务：`clinic_service_radius_semantics`（disclosure → CLINIC_RADIUS_SEMANTICS）
- 路由关键词：社区卫生服务、诊所覆盖、基层医疗、卫生站覆盖、clinic coverage、primary care access
- 内容指纹：`6a970467965113eb…`

### `epidemic_density_monitor` — 疫情密度监测

病例/事件点位密度监测（描述性 + 时序）：隐私聚合与显著性守卫。

- 任务族：`concentration_analysis`, `temporal_trend`
- 主制图：`visual_heatmap`；辅：`point_overlay`
- 核心能力：`poi_query`, `kde_density`, `grid_binning`, `temporal_profile`, `point_profile`
- 数据角色：`subject`*（block）, `target_time`（`*` = 必选）
- 科学义务：`epidemic_privacy_aggregation`（disclosure → EPIDEMIC_PRIVACY_AGGREGATION）；`epidemic_density_not_significance`（disclosure → DENSITY_VISUAL_NOT_SIGNIFICANCE）
- 语义回退：`PRIVACY_AGGREGATION_MIN_POINTS` → aggregate_grid（degraded）
- 路由关键词：病例分布、疫情监测、传染病密度、epidemic monitoring、case density
- 内容指纹：`1fc9a3724279b755…`

### `health_resource_per_capita` — 医疗资源人均画像

行政区医疗资源人均配置（床位/人员/机构 per 万人）：分母义务。

- 任务族：`spatial_equity`, `administrative_statistic`
- 主制图：`administrative_choropleth`
- 核心能力：`poi_query`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`, `category_breakdown`
- 数据角色：`subject`*（block）, `boundary`*（block）, `denominator`*（block）（`*` = 必选）
- 科学义务：`health_per_capita_denominator`（denominator → PER_CAPITA_DENOMINATOR_REQUIRED）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → count_map（degraded）
- 路由关键词：每万人、千人均、医疗资源人均、床位人均、health resources per capita
- 内容指纹：`025aa2e08d1484f5…`

### `hospital_service_area_stats` — 医院服务区统计

医院服务区（车程圈）覆盖与重叠统计：圈层口径与重叠语义披露。

- 任务族：`accessibility_analysis`, `administrative_statistic`
- 主制图：`proximity_overlay`；辅：`administrative_choropleth`
- 核心能力：`poi_query`, `service_area`, `admin_boundary_query`, `admin_aggregation`, `geometry_overlay`, `point_profile`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：医院服务范围、服务圈统计、就医圈、hospital service area
- 内容指纹：`f2ea3d00740a8e19…`

## remote_sensing

### `builtup_index_screening` — 不透水面/建成区指数筛查

建成区/不透水面指数（NDBI 类）筛查：波段语义义务 + 与绿度指数交叉验证建议。

- 任务族：`vegetation_index`, `raster_distribution`, `suitability_assessment`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `band_math`, `spectral_index`, `raster_reclassify`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`rs_band_semantics_required`（precondition → RS_BAND_SEMANTICS_REQUIRED）
- 语义回退：`RS_BAND_SEMANTICS_REQUIRED` → raster_view（degraded）
- 路由关键词：不透水面、建成区、ndbi、硬化地面、built-up index、impervious surface
- 内容指纹：`93334f5a5d540fc4…`

### `index_time_series_trend` — 指数时序趋势

光谱指数多期时序趋势（如 NDVI 年际趋势）：观测期数义务（≥4 期）。

- 任务族：`temporal_trend`, `vegetation_index`
- 主制图：`raster_surface`；辅：`point_overlay`
- 核心能力：`raster_source`, `spectral_index`, `temporal_trend`, `temporal_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`trend_min_observations`（precondition → TREND_INSUFFICIENT_OBSERVATIONS）
- 语义回退：`TREND_INSUFFICIENT_OBSERVATIONS` → bitemporal_difference（degraded）
- 路由关键词：指数趋势、植被变化趋势、年际指数、index time series、ndvi trend
- 内容指纹：`b06127283bf7f3ba…`

### `landcover_categorical_map` — 地表覆盖分类图

土地利用/地表覆盖分类专题：类别语义与图例义务，类别面积台账。

- 任务族：`raster_distribution`, `categorical_distribution`
- 主制图：`raster_surface`；辅：`administrative_aggregation`
- 核心能力：`raster_source`, `raster_reclassify`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`subject`*（block）（`*` = 必选）
- 路由关键词：土地利用、地表覆盖、地类图、land cover、land cover、land use map
- 内容指纹：`745e5f43a1bc6c15…`

### `nbr_burn_severity` — NBR 火烧迹地指数

NBR/dNBR 火烧严重度评价：基期+目标期双景义务，波段语义必须。

- 任务族：`vegetation_index`, `change_detection`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `spectral_index`, `band_math`, `raster_change_detection`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`rs_band_semantics_required`（precondition → RS_BAND_SEMANTICS_REQUIRED）；`burn_baseline_required`（temporal → BURN_BASELINE_SCENE_REQUIRED）
- 语义回退：`BURN_BASELINE_SCENE_REQUIRED` → nbr_single_date（degraded）
- 路由关键词：nbr、火烧迹地、过火面积、燃烧严重度、nbr、burn severity、dnbr
- 内容指纹：`54fea4e95dfbb287…`

### `ndvi_vegetation_monitor` — NDVI 植被监测

NDVI 植被指数计算与分级制图：波段语义义务 + 取值范围校验披露。

- 任务族：`vegetation_index`, `raster_distribution`
- 主制图：`raster_surface`；辅：`administrative_aggregation`
- 核心能力：`raster_source`, `ndvi`, `spectral_index`, `zonal_statistics`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`rs_band_semantics_required`（precondition → RS_BAND_SEMANTICS_REQUIRED）
- 语义回退：`RS_BAND_SEMANTICS_REQUIRED` → raster_view（degraded）
- 路由关键词：ndvi、植被覆盖、绿度、植被指数、ndvi、vegetation index
- 内容指纹：`88a37a1682bfff99…`

### `ndwi_water_index` — NDWI 水体指数

NDWI 水体提取与面积统计：波段语义义务 + 阈值敏感性披露。

- 任务族：`vegetation_index`, `raster_distribution`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `spectral_index`, `band_math`, `zonal_statistics`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`rs_band_semantics_required`（precondition → RS_BAND_SEMANTICS_REQUIRED）；`water_index_threshold`（disclosure → WATER_INDEX_THRESHOLD_SENSITIVITY）
- 语义回退：`RS_BAND_SEMANTICS_REQUIRED` → raster_view（degraded）
- 路由关键词：ndwi、水体提取、水面识别、ndwi、water index
- 内容指纹：`0fcf5e35cd2e5907…`

### `optical_bitemporal_change` — 双时相光学变化检测

两期影像变化检测（差值/变化向量）：配准与辐射一致性义务。

- 任务族：`change_detection`
- 主制图：`raster_surface`；辅：`administrative_aggregation`
- 核心能力：`raster_source`, `raster_change_detection`, `zonal_statistics`
- 数据角色：`baseline`*（block）, `target_time`*（block）（`*` = 必选）
- 科学义务：`change_registration_required`（transformation → CHANGE_REGISTRATION_REQUIRED）
- 路由关键词：影像对比、两期影像、变化图斑、image change detection、bitemporal
- 内容指纹：`36ae04ea91ea5836…`

### `rs_scene_inventory` — 影像数据清单

区域影像/栅格数据资产清单（覆盖范围、时相、波段）：数据画像义务。

- 任务族：`raster_distribution`, `distribution_overview`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `point_profile`
- 数据角色：`subject`*（block）（`*` = 必选）
- 路由关键词：影像清单、数据资产、影像覆盖、imagery inventory、scene catalog
- 内容指纹：`61dc7b5fbfc0e3b4…`

### `zonal_rs_index_report` — 分区遥感指数报表

行政区/格网尺度的遥感指数统计报表（均值/极值 + 排名）。

- 任务族：`vegetation_index`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`raster_surface`
- 核心能力：`raster_source`, `ndvi`, `spectral_index`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：各区植被指数、分区均值、指数报表、zonal index report
- 内容指纹：`97c52f4d2ab28316…`

## risk

### `earthquake_exposure_screening` — 地震暴露筛查

地震影响（烈度/断层缓冲）× 人口/建筑暴露筛查：数据可得性诚实降级。

- 任务族：`risk_exposure`
- 主制图：`proximity_overlay`；辅：`administrative_choropleth`
- 核心能力：`geometry_buffer`, `multi_ring_buffer`, `geometry_overlay`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`hazard`*（block）, `receptor`, `boundary`*（block）（`*` = 必选）
- 科学义务：`seismic_proxy_disclosure`（disclosure → SEISMIC_PROXIMITY_PROXY）；`risk_receptors_confirmed`（disclosure → RISK_RECEPTORS_UNCONFIRMED）
- 语义回退：`RISK_RECEPTORS_UNCONFIRMED` → hazard_map（degraded）
- 路由关键词：地震、断层、烈度、地震暴露、earthquake exposure、seismic hazard
- 内容指纹：`dcd5c0c73d126bbd…`

### `flood_risk_assessment` — 洪水风险评估

洪水淹没范围 × 受体暴露：几何淹没近似披露 + 受体义务。

- 任务族：`risk_exposure`, `watershed_analysis`
- 主制图：`raster_surface`；辅：`proximity_overlay`
- 核心能力：`raster_source`, `terrain_hydrology`, `raster_reclassify`, `geometry_overlay`, `zonal_statistics`, `poi_query`
- 数据角色：`hazard`*（block）, `receptor`, `boundary`*（block）（`*` = 必选）
- 科学义务：`flood_geometric_approximation`（disclosure → FLOOD_GEOMETRIC_APPROXIMATION）；`risk_receptors_confirmed`（disclosure → RISK_RECEPTORS_UNCONFIRMED）
- 语义回退：`RISK_RECEPTORS_UNCONFIRMED` → hazard_map（degraded）
- 路由关键词：洪水风险、洪涝、淹没风险、内涝风险、flood risk
- 内容指纹：`2973777b70b29a5b…`

### `hazard_exposure_overlay` — 通用危险-暴露叠加

通用 hazard × receptor 叠加暴露评价：受体义务与降级语义显式。

- 任务族：`risk_exposure`
- 主制图：`proximity_overlay`；辅：`administrative_choropleth`
- 核心能力：`geometry_buffer`, `geometry_overlay`, `spatial_join`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`hazard`*（block）, `receptor`, `boundary`*（block）（`*` = 必选）
- 科学义务：`risk_receptors_confirmed`（disclosure → RISK_RECEPTORS_UNCONFIRMED）
- 语义回退：`RISK_RECEPTORS_UNCONFIRMED` → hazard_map（degraded）
- 路由关键词：暴露评价、影响范围叠加、危险源影响、hazard exposure、exposure overlay
- 内容指纹：`26f6ad1fd89fc9ea…`

### `landslide_risk_assessment` — 滑坡风险评估

滑坡易发性 × 承灾体暴露：地形因子 + 危险分区 + 受体叠加；缺受体降级易发性图。

- 任务族：`risk_exposure`, `terrain_analysis`, `suitability_assessment`
- 主制图：`raster_surface`；辅：`proximity_overlay`
- 核心能力：`raster_source`, `terrain_slope`, `raster_reclassify`, `geometry_overlay`, `zonal_statistics`, `poi_query`
- 数据角色：`hazard`*（block）, `receptor`, `boundary`*（block）（`*` = 必选）
- 科学义务：`risk_receptors_confirmed`（disclosure → RISK_RECEPTORS_UNCONFIRMED）；`landslide_factor_disclosure`（disclosure → LANDSLIDE_FACTOR_SEMANTICS）
- 语义回退：`RISK_RECEPTORS_UNCONFIRMED` → hazard_map（degraded）
- 路由关键词：滑坡、地质灾害、崩塌、滑坡风险、landslide risk、geological hazard
- 内容指纹：`48149cfb1a1e4da1…`

### `multi_hazard_composite` — 多灾种复合筛查

多灾种危险区复合（取大/加权合成）：合成语义必须披露（复合≠概率相加）。

- 任务族：`risk_exposure`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `raster_reclassify`, `band_math`, `geometry_overlay`, `zonal_statistics`
- 数据角色：`hazard`*（block）, `receptor`（`*` = 必选）
- 科学义务：`multi_hazard_composition_semantics`（disclosure → MULTI_HAZARD_COMPOSITION_SEMANTICS）
- 路由关键词：多灾种、复合灾害、综合风险区、multi hazard、composite hazard
- 内容指纹：`a63cf72e52596f20…`

### `urban_fire_risk_hotspot` — 火险热点分析

建筑/绿地火险因子叠加与热点筛选（历史火点密度代理）。

- 任务族：`risk_exposure`, `concentration_analysis`
- 主制图：`hotspot_overlay`；辅：`raster_surface`
- 核心能力：`poi_query`, `kde_density`, `raster_reclassify`, `geometry_overlay`, `getis_ord_gi_star`
- 数据角色：`hazard`*（block）, `receptor`（`*` = 必选）
- 科学义务：`fire_density_is_hazard_proxy`（disclosure → FIRE_DENSITY_IS_HAZARD_PROXY）
- 路由关键词：火险、火灾风险、消防风险、高火险区、fire risk、wildfire hotspot
- 内容指纹：`e211e481dbbbbaf9…`

## sar

### `insar_deformation_screening` — InSAR 形变筛查

InSAR 地表形变筛查：干涉处理链（planned 能力诚实呈现），不可用时阻断或转述外部产品。

- 任务族：`sar_analysis`, `risk_exposure`
- 主制图：`raster_surface`；辅：`point_overlay`
- 核心能力：`raster_source`, `sar_analysis`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`insar_processing_chain_planned`（temporal → INSAR_STACK_INSUFFICIENT）；`insar_temporal_baseline`（temporal → INSAR_TEMPORAL_BASELINE_REQUIRED）
- 语义回退：`INSAR_STACK_INSUFFICIENT` → summary（not_allowed）
- 路由关键词：insar、形变、沉降监测、地面沉降、干涉、insar、deformation mapping、subsidence
- 内容指纹：`2f97512293e3656f…`

### `sar_backscatter_overview` — SAR 后向散射概览

SAR 影像后向散射强度概览（描述性）：极化语义披露，不做定量跨景比较。

- 任务族：`sar_analysis`, `raster_distribution`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `sar_analysis`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`sar_polarization_disclosure`（disclosure → SAR_POLARIZATION_SEMANTICS）
- 路由关键词：sar、后向散射、雷达影像、sar backscatter、sar overview
- 内容指纹：`f58953c5c6cf6b05…`

### `sar_calibrated_comparison` — SAR 定标对比

跨景 SAR 定量比较：辐射定标证据义务（planned 前置诚实呈现）；未定标降级描述比较。

- 任务族：`sar_analysis`, `change_detection`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `sar_analysis`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`sar_calibration_evidence`（transformation → SAR_CALIBRATION_EVIDENCE_REQUIRED）；`sar_speckle_preprocess`（transformation → SAR_SPECKLE_FILTER_PLANNED）
- 语义回退：`SAR_CALIBRATION_EVIDENCE_REQUIRED` → descriptive_relative_comparison（proxy）
- 路由关键词：sar对比、定标、绝对后向散射、辐射定标、sar calibration、calibrated comparison
- 内容指纹：`a048883383e50fae…`

### `sar_change_detection_workflow` — SAR 变化检测

双时相 SAR 变化检测（强度差/比值）：定标义务 + 相干性义务披露。

- 任务族：`sar_analysis`, `change_detection`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `sar_analysis`, `raster_change_detection`
- 数据角色：`subject`*（block）, `comparison_time`*（`*` = 必选）
- 科学义务：`sar_calibration_evidence`（transformation → SAR_CALIBRATION_EVIDENCE_REQUIRED）；`sar_change_dual_date`（temporal → SAR_CHANGE_DUAL_DATE_REQUIRED）
- 语义回退：`SAR_CHANGE_DUAL_DATE_REQUIRED` → single_date_view（degraded）
- 路由关键词：sar变化、雷达变化检测、sar change detection
- 内容指纹：`5f1e33856d06b500…`

### `sar_flood_mapping` — SAR 洪水制图

SAR 洪水范围制图（水体镜面反射低后向散射）：阈值敏感性与定标义务。

- 任务族：`sar_analysis`, `risk_exposure`, `watershed_analysis`
- 主制图：`raster_surface`；辅：`proximity_overlay`
- 核心能力：`raster_source`, `sar_analysis`, `raster_reclassify`, `zonal_statistics`
- 数据角色：`subject`*（block）（`*` = 必选）
- 科学义务：`sar_calibration_evidence`（transformation → SAR_CALIBRATION_EVIDENCE_REQUIRED）；`sar_flood_threshold`（disclosure → SAR_FLOOD_THRESHOLD_SENSITIVITY）
- 路由关键词：sar洪水、洪水范围、淹没提取、sar flood mapping
- 内容指纹：`78c816208615bced…`

## site_selection

### `ev_charger_site_selection` — 充电站选址评价

充电站/桩选址：交通流量 + 电网/用地代理准则 + MCDA。

- 任务族：`site_selection`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `traffic_status`, `geometry_buffer`, `mcda_evaluation`
- 数据角色：`subject`*（block）, `boundary`*（block）, `criteria`, `constraint`（`*` = 必选）
- 科学义务：`site_criteria_declared`（disclosure → SITE_SELECTION_CRITERIA_UNDECLARED）；`site_weight_provenance`（disclosure → SITE_WEIGHT_PROVENANCE）
- 语义回退：`SITE_SELECTION_CRITERIA_UNDECLARED` → constraint_screening（degraded）
- 路由关键词：充电站选址、充电桩布局、ev charging site selection
- 内容指纹：`2d121e28c7636b2d…`

### `generic_mcda_site_ranking` — 通用多准则选址

通用 MCDA 选址排序（WSM/TOPSIS）：准则+权重来源义务，缺准则不排序。

- 任务族：`site_selection`
- 主制图：`proximity_overlay`
- 核心能力：`mcda_evaluation`, `geometry_buffer`, `proximity_buffer`, `admin_boundary_query`
- 数据角色：`subject`*（block）, `criteria`, `constraint`（`*` = 必选）
- 科学义务：`site_criteria_declared`（disclosure → SITE_SELECTION_CRITERIA_UNDECLARED）；`site_weight_provenance`（disclosure → SITE_WEIGHT_PROVENANCE）
- 语义回退：`SITE_SELECTION_CRITERIA_UNDECLARED` → constraint_screening（degraded）
- 路由关键词：多准则、加权评分、候选排序、方案比选、mcda、weighted ranking、topsis
- 内容指纹：`df8edf094895ba51…`

### `hospital_site_selection` — 医院选址评价

新医院选址：急救响应覆盖 + 人口需求 + MCDA；缺分母只做空间准则排序。

- 任务族：`site_selection`, `accessibility_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `service_area`, `closest_facility`, `mcda_evaluation`, `admin_boundary_query`
- 数据角色：`subject`*（block）, `boundary`*（block）, `criteria`, `constraint`, `population`（`*` = 必选）
- 科学义务：`site_criteria_declared`（disclosure → SITE_SELECTION_CRITERIA_UNDECLARED）；`site_weight_provenance`（disclosure → SITE_WEIGHT_PROVENANCE）
- 语义回退：`SITE_SELECTION_CRITERIA_UNDECLARED` → constraint_screening（degraded）
- 路由关键词：医院选址、新院区、建医院、hospital site selection
- 内容指纹：`18a3f6290f4feb8d…`

### `landfill_site_screening` — 填埋场选址筛查

固废/填埋场选址筛查：强约束（避让水源/居民区）+ 准则排序。

- 任务族：`site_selection`, `suitability_assessment`
- 主制图：`proximity_overlay`；辅：`raster_surface`
- 核心能力：`geometry_buffer`, `multi_ring_buffer`, `geometry_overlay`, `mcda_evaluation`, `admin_boundary_query`
- 数据角色：`subject`*（block）, `boundary`*（block）, `constraint`（`*` = 必选）
- 科学义务：`site_criteria_declared`（disclosure → SITE_SELECTION_CRITERIA_UNDECLARED）；`site_weight_provenance`（disclosure → SITE_WEIGHT_PROVENANCE）
- 语义回退：`SITE_SELECTION_CRITERIA_UNDECLARED` → constraint_screening（degraded）
- 路由关键词：填埋场选址、垃圾站选址、固废设施选址、landfill site、waste facility siting
- 内容指纹：`17c552cc08644f81…`

### `school_site_selection` — 学校选址评价

新学校选址：服务盲区约束 + 人口可达 + MCDA 排序；准则未声明不排序。

- 任务族：`site_selection`, `accessibility_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `service_area`, `geometry_buffer`, `mcda_evaluation`, `admin_boundary_query`
- 数据角色：`subject`*（block）, `boundary`*（block）, `criteria`, `constraint`, `population`（`*` = 必选）
- 科学义务：`site_criteria_declared`（disclosure → SITE_SELECTION_CRITERIA_UNDECLARED）；`site_weight_provenance`（disclosure → SITE_WEIGHT_PROVENANCE）
- 语义回退：`SITE_SELECTION_CRITERIA_UNDECLARED` → constraint_screening（degraded）
- 路由关键词：学校选址、新校址、建学校、school site selection
- 内容指纹：`dd3af178f4e3365a…`

### `shelter_site_selection` — 避难场所选址

应急避难场所选址：覆盖盲区 + 安全约束（避让灾害源）+ MCDA。

- 任务族：`site_selection`, `risk_exposure`, `accessibility_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `geometry_buffer`, `service_area`, `mcda_evaluation`, `admin_boundary_query`
- 数据角色：`subject`*（block）, `boundary`*（block）, `criteria`, `constraint`（`*` = 必选）
- 科学义务：`site_criteria_declared`（disclosure → SITE_SELECTION_CRITERIA_UNDECLARED）；`site_weight_provenance`（disclosure → SITE_WEIGHT_PROVENANCE）
- 语义回退：`SITE_SELECTION_CRITERIA_UNDECLARED` → constraint_screening（degraded）
- 路由关键词：避难场所选址、避难所布局、应急场所选址、shelter site selection
- 内容指纹：`9d6c9d20a1077103…`

## statistics

### `admin_ranking_stats` — 行政区排名统计

行政区指标排名（Top-N/排序表 + 分级填色），数值字段语义显式声明。

- 任务族：`administrative_statistic`
- 主制图：`administrative_choropleth`
- 核心能力：`admin_boundary_query`, `admin_aggregation`, `category_breakdown`
- 数据角色：`subject`*（block）, `boundary`*（block）, `measure`*（block）（`*` = 必选）
- 路由关键词：排名、top、最多最少、统计排名、ranking by district、top n
- 内容指纹：`17b6ce0c0e939c2f…`

### `categorical_composition_stats` — 类别构成统计

类别构成/占比结构（图表 + 分类专题），构成语义与密度语义分离。

- 任务族：`categorical_distribution`
- 主制图：`categorical_thematic`
- 核心能力：`poi_query`, `category_breakdown`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`subject`*（block）（`*` = 必选）
- 路由关键词：构成比、结构统计、类型占比、composition、category share
- 内容指纹：`95f4bcf318b24468…`

### `geary_global_autocorrelation` — 全局 Geary's C 检验

Geary's C 全局自相关（对局部差异更敏感），与 Moran's I 互为佐证。

- 任务族：`spatial_autocorrelation`
- 主制图：`administrative_choropleth`
- 核心能力：`admin_boundary_query`, `admin_aggregation`, `global_gearys_c`
- 数据角色：`subject`*（block）, `boundary`*（block）, `measure`*（block）（`*` = 必选）
- 科学义务：`geary_numeric_field`（precondition → GEARY_NUMERIC_FIELD_REQUIRED）
- 路由关键词：geary、geary c、geary检验、geary
- 内容指纹：`73013e2ba9dc1174…`

### `general_g_cluster_analysis` — General G 聚集度分析

General G 全局聚集度检验：高值聚集 vs 低值聚集的全局判断。

- 任务族：`spatial_autocorrelation`
- 主制图：`administrative_choropleth`
- 核心能力：`admin_boundary_query`, `admin_aggregation`, `general_g`
- 数据角色：`subject`*（block）, `boundary`*（block）, `measure`*（block）（`*` = 必选）
- 科学义务：`general_g_numeric_field`（precondition → GENERAL_G_NUMERIC_FIELD_REQUIRED）
- 路由关键词：general g、聚集度检验、高低值聚集、general g
- 内容指纹：`47327e9abb1d56ca…`

### `getis_ord_hotspot_significance` — Getis-Ord Gi* 显著性热点

Gi* 统计显著性热点/冷点图：只有检验条件满足才出显著结论，否则降级描述性密度。

- 任务族：`concentration_analysis`, `spatial_autocorrelation`
- 主制图：`hotspot_overlay`；辅：`administrative_choropleth`
- 核心能力：`admin_boundary_query`, `admin_aggregation`, `getis_ord_gi_star`
- 数据角色：`subject`*（block）, `boundary`*（block）, `measure`*（block）（`*` = 必选）
- 科学义务：`gi_star_numeric_field`（precondition → GI_STAR_NUMERIC_FIELD_REQUIRED）；`gi_star_min_units`（precondition → GI_STAR_INSUFFICIENT_UNITS）
- 语义回退：`GI_STAR_INSUFFICIENT_UNITS` → visual_density（degraded）; `HOTSPOT_SIGNIFICANCE_UNAVAILABLE` → visual_density（degraded）
- 路由关键词：显著性热点、gi星、getis、热点检验、统计热点、getis-ord、gi star、statistically significant hotspot
- 内容指纹：`d93f588bffd7bb60…`

### `global_moran_autocorrelation` — 全局空间自相关（Moran's I）

全局 Moran's I 检验：数值字段 + 空间权重选择 + 显著性披露；权重方案与距离度量（含地理坐标下的投影/反距离处理）必须披露。

- 任务族：`spatial_autocorrelation`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`hotspot_overlay`
- 核心能力：`admin_boundary_query`, `admin_aggregation`, `global_morans_i`
- 数据角色：`subject`*（block）, `boundary`*（block）, `measure`*（block）（`*` = 必选）
- 科学义务：`autocorr_numeric_field`（precondition → AUTOCORR_NUMERIC_FIELD_REQUIRED）；`autocorr_min_units`（precondition → AUTOCORR_INSUFFICIENT_UNITS）；`autocorr_multiple_testing_note`（disclosure → AUTOCORR_MULTIPLE_TESTING）
- 语义回退：`AUTOCORR_INSUFFICIENT_UNITS` → descriptive_summary（degraded）
- 路由关键词：全局自相关、莫兰指数、moran、空间自相关、global moran、spatial autocorrelation
- 内容指纹：`d045623de6adace0…`

### `local_moran_lisa` — 局部自相关聚类图（LISA）

Local Moran's I 聚类图（HH/LL/HL/LH 四象限）：多重比较校正与聚类显著性披露为强制义务。

- 任务族：`spatial_autocorrelation`, `concentration_analysis`
- 主制图：`hotspot_overlay`；辅：`administrative_choropleth`
- 核心能力：`admin_boundary_query`, `admin_aggregation`, `local_morans_i`
- 数据角色：`subject`*（block）, `boundary`*（block）, `measure`*（block）（`*` = 必选）
- 科学义务：`lisa_numeric_field`（precondition → LISA_NUMERIC_FIELD_REQUIRED）；`lisa_min_units`（precondition → LISA_INSUFFICIENT_UNITS）；`lisa_multiple_testing`（disclosure → LISA_MULTIPLE_TESTING）
- 语义回退：`LISA_INSUFFICIENT_UNITS` → descriptive_summary（degraded）
- 路由关键词：局部自相关、lisa、聚集图、冷点热点区、lisa、local moran、cluster map
- 内容指纹：`2573552230e09650…`

### `rate_aggregation_choropleth` — 比率归一化专题图

率/比值专题（人均/地均/占比）：分母必选，缺分母时确定性降级为计数并披露。

- 任务族：`administrative_statistic`, `analytical_density`, `spatial_equity`
- 主制图：`administrative_choropleth`；辅：`point_overlay`
- 核心能力：`admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）, `denominator`*（block）（`*` = 必选）
- 科学义务：`rate_requires_denominator`（denominator → RATE_MISSING_DENOMINATOR）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → raw_counts（degraded）
- 路由关键词：人均、地均、占比统计、率图、per capita、rate map、normalized ratio
- 内容指纹：`1560d6126fa64faa…`

### `spatiotemporal_cluster_detection` — 时空聚类检测

带时间戳的时空聚类（spatiotemporal clustering）：时间字段义务 + 聚类参数披露。

- 任务族：`concentration_analysis`, `temporal_trend`
- 主制图：`hotspot_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `spatiotemporal_clustering`, `temporal_profile`, `point_profile`
- 数据角色：`subject`*（block）, `target_time`*（`*` = 必选）
- 科学义务：`st_clustering_temporal_field`（temporal → ST_CLUSTERING_TEMPORAL_FIELD_REQUIRED）
- 语义回退：`DATA_ROLE_MISSING_TARGET_TIME` → spatial_clusters（approximation）
- 路由关键词：时空聚类、时空聚集、聚集演化、spatiotemporal clustering
- 内容指纹：`78dca92a8b934a7a…`

### `zonal_profile_statistics` — 分区统计画像

以行政区/格网为单元的 zonal 统计画像（栅格均值/总量落表）。

- 任务族：`administrative_statistic`, `raster_distribution`
- 主制图：`administrative_choropleth`；辅：`raster_surface`
- 核心能力：`admin_boundary_query`, `raster_source`, `zonal_statistics`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：分区统计、各区的均值、栅格统计、zonal statistics
- 内容指纹：`5759eb0501b5806a…`

## suitability

### `agriculture_suitability` — 农业适宜性评价

耕作/种植适宜性（土壤/坡度/水热代理因子）：因子语义与数据年代披露。

- 任务族：`suitability_assessment`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `terrain_slope`, `ndvi`, `raster_reclassify`, `geometry_overlay`, `mcda_evaluation`
- 数据角色：`subject`*（block）, `criteria`, `constraint`（`*` = 必选）
- 科学义务：`suitability_factors_declared`（disclosure → SUITABILITY_FACTORS_UNDECLARED）；`suitability_weight_provenance`（disclosure → SUITABILITY_WEIGHT_PROVENANCE）；`suitability_normalization_semantics`（disclosure → SUITABILITY_NORMALIZATION_SEMANTICS）
- 语义回退：`SUITABILITY_FACTORS_UNDECLARED` → constraint_screening（degraded）
- 路由关键词：农业适宜、耕作适宜、种植适宜区、agricultural suitability
- 内容指纹：`4201aca52f7bddc4…`

### `construction_suitability` — 建设适宜性评价

城镇建设适宜性（地形/地质/交通/用地因子加权）：因子与权重来源义务。

- 任务族：`suitability_assessment`, `site_selection`
- 主制图：`raster_surface`；辅：`administrative_aggregation`
- 核心能力：`raster_source`, `terrain_slope`, `raster_reclassify`, `geometry_overlay`, `mcda_evaluation`, `zonal_statistics`
- 数据角色：`subject`*（block）, `boundary`*（block）, `criteria`, `constraint`（`*` = 必选）
- 科学义务：`suitability_factors_declared`（disclosure → SUITABILITY_FACTORS_UNDECLARED）；`suitability_weight_provenance`（disclosure → SUITABILITY_WEIGHT_PROVENANCE）；`suitability_normalization_semantics`（disclosure → SUITABILITY_NORMALIZATION_SEMANTICS）
- 语义回退：`SUITABILITY_FACTORS_UNDECLARED` → constraint_screening（degraded）
- 路由关键词：建设适宜性、适建区、开发适宜、宜建区、construction suitability
- 内容指纹：`70e196d86f64a154…`

### `ecological_suitability` — 生态保护适宜性

生态重要性/保护适宜性分区（水源涵养/植被覆盖代理因子）。

- 任务族：`suitability_assessment`
- 主制图：`raster_surface`；辅：`administrative_choropleth`
- 核心能力：`raster_source`, `ndvi`, `terrain_slope`, `raster_reclassify`, `geometry_overlay`, `mcda_evaluation`
- 数据角色：`subject`*（block）, `boundary`*（block）, `criteria`, `constraint`（`*` = 必选）
- 科学义务：`suitability_factors_declared`（disclosure → SUITABILITY_FACTORS_UNDECLARED）；`suitability_weight_provenance`（disclosure → SUITABILITY_WEIGHT_PROVENANCE）；`suitability_normalization_semantics`（disclosure → SUITABILITY_NORMALIZATION_SEMANTICS）
- 语义回退：`SUITABILITY_FACTORS_UNDECLARED` → constraint_screening（degraded）
- 路由关键词：生态适宜、生态保护区、重要生态空间、ecological suitability
- 内容指纹：`995f2b94181cbd5f…`

### `tourism_suitability` — 旅游开发适宜性

旅游资源开发适宜性（景观可达/设施配套代理因子）。

- 任务族：`suitability_assessment`
- 主制图：`raster_surface`；辅：`proximity_overlay`
- 核心能力：`raster_source`, `service_area`, `raster_reclassify`, `geometry_overlay`, `mcda_evaluation`, `zonal_statistics`
- 数据角色：`subject`*（block）, `boundary`*（block）, `criteria`, `constraint`（`*` = 必选）
- 科学义务：`suitability_factors_declared`（disclosure → SUITABILITY_FACTORS_UNDECLARED）；`suitability_weight_provenance`（disclosure → SUITABILITY_WEIGHT_PROVENANCE）；`suitability_normalization_semantics`（disclosure → SUITABILITY_NORMALIZATION_SEMANTICS）
- 语义回退：`SUITABILITY_FACTORS_UNDECLARED` → constraint_screening（degraded）
- 路由关键词：旅游适宜、景区开发、文旅适宜性、tourism suitability
- 内容指纹：`acecfb4f6fe35335…`

### `urban_expansion_suitability` — 城镇扩张适宜性

城镇开发边界/扩张适宜性评价（双评价思路）。

- 任务族：`suitability_assessment`
- 主制图：`raster_surface`；辅：`administrative_choropleth`
- 核心能力：`raster_source`, `terrain_slope`, `band_math`, `raster_reclassify`, `geometry_overlay`, `mcda_evaluation`
- 数据角色：`subject`*（block）, `boundary`*（block）, `criteria`, `constraint`（`*` = 必选）
- 科学义务：`suitability_factors_declared`（disclosure → SUITABILITY_FACTORS_UNDECLARED）；`suitability_weight_provenance`（disclosure → SUITABILITY_WEIGHT_PROVENANCE）；`suitability_normalization_semantics`（disclosure → SUITABILITY_NORMALIZATION_SEMANTICS）
- 语义回退：`SUITABILITY_FACTORS_UNDECLARED` → constraint_screening（degraded）
- 路由关键词：开发边界、扩张适宜、城镇开发适宜性、双评价、urban expansion suitability、double evaluation
- 内容指纹：`070cb0ea55e187c4…`

## temporal

### `changepoint_detection_workflow` — 突变点检测

时序突变点（changepoint）检测：需要长序列；短序列降级两期对比并披露。

- 任务族：`temporal_trend`, `change_detection`
- 主制图：`point_overlay`；辅：`administrative_choropleth`
- 核心能力：`temporal_profile`, `temporal_change_point`, `temporal_trend`
- 数据角色：`subject`*（block）, `target_time`*（`*` = 必选）
- 科学义务：`changepoint_min_observations`（precondition → CHANGEPOINT_INSUFFICIENT_SERIES）
- 语义回退：`CHANGEPOINT_INSUFFICIENT_SERIES` → bitemporal_difference（degraded）
- 路由关键词：突变点、拐点、转折点、变化节点、changepoint、breakpoint detection
- 内容指纹：`3d547416ecbc1381…`

### `interannual_comparison_workflow` — 年际对比分析

多年同期对比（去年 vs 今年等）：对比期角色显式声明，差异语义守卫。

- 任务族：`temporal_trend`, `change_detection`
- 主制图：`administrative_choropleth`；辅：`point_overlay`
- 核心能力：`temporal_profile`, `temporal_aggregate`, `admin_aggregation`, `admin_boundary_query`
- 数据角色：`subject`*（block）, `baseline`*, `comparison_time`*（`*` = 必选）
- 科学义务：`interannual_baseline_required`（temporal → INTERANNUAL_BASELINE_REQUIRED）
- 语义回退：`DATA_ROLE_MISSING_BASELINE` → current_snapshot（degraded）
- 路由关键词：同比、去年对比、年际对比、和去年比、year over year、interannual comparison
- 内容指纹：`ee6b8297bb467dfc…`

### `linear_trend_analysis` — 线性趋势分析

多期观测线性趋势（斜率+显著性）：观测期数义务（≥4），两期不得称趋势。

- 任务族：`temporal_trend`
- 主制图：`administrative_choropleth`；辅：`point_overlay`
- 核心能力：`temporal_profile`, `temporal_trend`, `admin_aggregation`, `admin_boundary_query`
- 数据角色：`subject`*（block）, `target_time`*（`*` = 必选）
- 科学义务：`trend_min_observations`（precondition → TREND_INSUFFICIENT_OBSERVATIONS）；`trend_temporal_field`（temporal → TREND_TEMPORAL_FIELD_REQUIRED）
- 语义回退：`TREND_INSUFFICIENT_OBSERVATIONS` → bitemporal_difference（degraded）
- 路由关键词：变化趋势、趋势分析、逐年变化、年际趋势、trend analysis、temporal trend
- 内容指纹：`374df5bf3db279df…`

### `seasonal_pattern_analysis` — 季节性模式分析

周期/季节性模式识别（月度/季度聚合）：至少一个完整周期的观测义务。

- 任务族：`temporal_trend`
- 主制图：`administrative_choropleth`；辅：`point_overlay`
- 核心能力：`temporal_profile`, `temporal_aggregate`, `temporal_trend`
- 数据角色：`subject`*（block）, `target_time`*（`*` = 必选）
- 科学义务：`seasonal_full_cycle`（temporal → SEASONAL_CYCLE_INCOMPLETE）
- 语义回退：`SEASONAL_CYCLE_INCOMPLETE` → descriptive_summary（degraded）
- 路由关键词：季节性、月度模式、周期分析、旺季淡季、seasonality、seasonal pattern
- 内容指纹：`5db753ded8fd01b1…`

### `temporal_aggregate_stats` — 时段聚合统计

按日/月/年聚合的时段统计（总量/均值）+ 空间分布联动。

- 任务族：`temporal_trend`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`point_overlay`
- 核心能力：`temporal_aggregate`, `admin_aggregation`, `admin_boundary_query`, `point_profile`
- 数据角色：`subject`*（block）, `target_time`*（`*` = 必选）
- 路由关键词：按月统计、按年汇总、时段统计、temporal aggregation、monthly stats
- 内容指纹：`23011dd413c36536…`

### `temporal_profile_station` — 站点时序画像

单站/多站观测时序画像（折线+点位标注）：时间字段义务，趋势声明守卫。

- 任务族：`temporal_trend`, `simple_view`
- 主制图：`point_overlay`
- 核心能力：`poi_query`, `temporal_profile`, `point_profile`
- 数据角色：`subject`*（block）, `target_time`*（`*` = 必选）
- 科学义务：`profile_temporal_field`（temporal → PROFILE_TEMPORAL_FIELD_REQUIRED）
- 语义回退：`DATA_ROLE_MISSING_TARGET_TIME` → snapshot_view（degraded）
- 路由关键词：时序曲线、时间变化、监测时序、time profile、temporal curve
- 内容指纹：`e431bc5ac54291a3…`

## terrain

### `aspect_analysis_workflow` — 坡向分析

DEM 坡向衍生（方位分类）：阳坡/阴坡等向别分类制图。

- 任务族：`terrain_analysis`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `terrain_aspect`, `terrain_derivatives`
- 数据角色：`elevation`*（block）（`*` = 必选）
- 科学义务：`terrain_dem_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`terrain_crs_metric`（precondition → TERRAIN_METRIC_CRS_REQUIRED）
- 语义回退：`TERRAIN_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：坡向、阴阳坡、朝向分析、aspect analysis
- 内容指纹：`f2b567580d1d1751…`

### `contour_map_product` — 等高线产品

等高线（isoline）地形产品：等距参数披露 + 与 DEM 面图层联动。

- 任务族：`terrain_analysis`, `simple_view`, `raster_distribution`
- 主制图：`isoline_contour`；辅：`raster_surface`
- 核心能力：`raster_source`, `terrain_contours`, `terrain_derivatives`
- 数据角色：`elevation`*（block）（`*` = 必选）
- 科学义务：`terrain_dem_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`terrain_crs_metric`（precondition → TERRAIN_METRIC_CRS_REQUIRED）
- 语义回退：`TERRAIN_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：等高线、等值线、contour、contour map、isoline
- 内容指纹：`1f646081f620ee6d…`

### `hillshade_cartography` — 山体阴影渲染

山体阴影（hillshade）地形渲染：光源方位/高度角参数披露。

- 任务族：`terrain_analysis`, `simple_view`, `raster_distribution`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `terrain_hillshade`, `terrain_derivatives`
- 数据角色：`elevation`*（block）（`*` = 必选）
- 科学义务：`terrain_dem_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`terrain_crs_metric`（precondition → TERRAIN_METRIC_CRS_REQUIRED）
- 语义回退：`TERRAIN_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：山体阴影、hillshade、晕渲图、地形渲染、hillshade、shaded relief
- 内容指纹：`275af02837c85c41…`

### `slope_analysis_workflow` — 坡度分析

DEM 坡度衍生 + 分级制图：米制 CRS 义务 + 单位（度/百分比）披露。

- 任务族：`terrain_analysis`
- 主制图：`raster_surface`；辅：`isoline_contour`
- 核心能力：`raster_source`, `terrain_slope`, `terrain_derivatives`, `zonal_statistics`
- 数据角色：`elevation`*（block）（`*` = 必选）
- 科学义务：`terrain_dem_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`terrain_crs_metric`（precondition → TERRAIN_METRIC_CRS_REQUIRED）
- 语义回退：`TERRAIN_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：坡度、坡度分析、陡坡、slope analysis、steepness
- 内容指纹：`0b9de426ff4a7294…`

### `slope_zoning_constraint` — 坡度分区约束

坡度分级约束区提取（如 >25° 禁建）：分级阈值披露 + 面积台账。

- 任务族：`terrain_analysis`, `suitability_assessment`
- 主制图：`raster_surface`；辅：`administrative_aggregation`
- 核心能力：`raster_source`, `terrain_slope`, `raster_reclassify`, `zonal_statistics`
- 数据角色：`elevation`*（block）（`*` = 必选）
- 科学义务：`terrain_dem_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`terrain_crs_metric`（precondition → TERRAIN_METRIC_CRS_REQUIRED）；`slope_threshold_disclosure`（disclosure → SLOPE_THRESHOLD_DECLARED）
- 语义回退：`TERRAIN_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：坡度分区、禁建区、陡坡约束、开垦限制、slope zoning、steep slope constraint
- 内容指纹：`18c4a3a5b1cef6aa…`

### `terrain_morphometry_suite` — 地形形态计量套件

坡度/坡向/起伏/曲率综合地形计量（多衍生对比面板）。

- 任务族：`terrain_analysis`
- 主制图：`raster_surface`
- 核心能力：`raster_source`, `terrain_derivatives`, `terrain_slope`, `terrain_aspect`, `terrain_hillshade`
- 数据角色：`elevation`*（block）（`*` = 必选）
- 科学义务：`terrain_dem_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`terrain_crs_metric`（precondition → TERRAIN_METRIC_CRS_REQUIRED）
- 语义回退：`TERRAIN_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：地形因子、形态计量、地形综合分析、terrain morphometry、terrain derivatives suite
- 内容指纹：`88bb67e2e6958dd0…`

### `terrain_relief_overview` — 地势起伏概览

区域地势起伏度概览（相对高程 + 分区起伏统计）。

- 任务族：`terrain_analysis`, `raster_distribution`
- 主制图：`raster_surface`；辅：`administrative_aggregation`
- 核心能力：`raster_source`, `terrain_derivatives`, `zonal_statistics`, `admin_boundary_query`
- 数据角色：`elevation`*（block）, `boundary`（`*` = 必选）
- 科学义务：`terrain_dem_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`terrain_crs_metric`（precondition → TERRAIN_METRIC_CRS_REQUIRED）
- 语义回退：`TERRAIN_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：地势、起伏度、高程分布、地形概览、terrain relief、elevation overview
- 内容指纹：`2749bfbbbd4b75eb…`

### `viewshed_analysis_workflow` — 视域/通视分析

观察点视域分析：DEM 质量 + 观察点高度参数披露；结果为可见/不可见分区。

- 任务族：`terrain_analysis`, `site_selection`
- 主制图：`raster_surface`；辅：`proximity_overlay`
- 核心能力：`raster_source`, `terrain_viewshed`, `terrain_derivatives`, `point_profile`
- 数据角色：`elevation`*（block）, `reference`*（block）（`*` = 必选）
- 科学义务：`terrain_dem_required`（precondition → TERRAIN_DEM_BAND_REQUIRED）；`terrain_crs_metric`（precondition → TERRAIN_METRIC_CRS_REQUIRED）
- 语义回退：`TERRAIN_METRIC_CRS_REQUIRED` → dem_view（degraded）; `TERRAIN_DEM_BAND_REQUIRED` → summary（not_allowed）
- 路由关键词：视域、通视、可视域、瞭望覆盖、viewshed、visibility analysis
- 内容指纹：`e828fb22b9be59c7…`

## transport

### `commute_flow_corridor` — 通勤流走廊分析

通勤 OD 走廊强度分析（主走廊识别 + 流量分级）：OD 数据口径披露。

- 任务族：`mobility_flow`
- 主制图：`flow_od_arc`；辅：`point_overlay`
- 核心能力：`poi_query`, `od_matrix`, `od_flow_mapping`, `point_profile`
- 数据角色：`subject`*（block）, `measure`（`*` = 必选）
- 路由关键词：通勤走廊、主通道、通勤od、commuting corridor
- 内容指纹：`ecd4e7603a833032…`

### `road_network_inventory` — 路网要素清单

路网/道路等级清单与里程统计（线要素台账）：里程口径披露。

- 任务族：`distribution_overview`, `administrative_statistic`
- 主制图：`categorical_thematic`；辅：`point_overlay`
- 核心能力：`poi_query`, `admin_boundary_query`, `admin_aggregation`, `category_breakdown`, `point_profile`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：路网清单、道路里程、道路等级、road network inventory
- 内容指纹：`7def67e603d822ce…`

### `station_catchment_profile` — 站点集散圈画像

轨道站点集散圈（步行/骑行接驳圈）覆盖画像：接驳方式口径披露。

- 任务族：`accessibility_analysis`, `proximity_analysis`
- 主制图：`proximity_overlay`；辅：`point_overlay`
- 核心能力：`poi_query`, `service_area`, `geometry_buffer`, `point_profile`, `admin_boundary_query`
- 数据角色：`subject`*（block）, `boundary`（`*` = 必选）
- 科学义务：`catchment_mode_disclosure`（disclosure → CATCHMENT_MODE_SEMANTICS）
- 路由关键词：站点集散圈、tod圈层、站城覆盖、station catchment、tod radius
- 内容指纹：`6f467c51c8c80da5…`

### `transit_service_coverage` — 公交服务覆盖

公交/轨道站点服务覆盖评价（站点缓冲 + 覆盖人口）：分母义务。

- 任务族：`accessibility_analysis`, `administrative_statistic`
- 主制图：`proximity_overlay`；辅：`administrative_choropleth`
- 核心能力：`poi_query`, `service_area`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）, `population`（`*` = 必选）
- 科学义务：`transit_coverage_denominator`（denominator → TRANSIT_COVERAGE_DENOMINATOR_REQUIRED）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → coverage_area_map（degraded）
- 路由关键词：公交覆盖、轨道服务、站点服务范围、transit service coverage
- 内容指纹：`441357ed15f9ac7c…`

## urban

### `greening_rate_admin` — 绿化率统计

行政区绿化率/绿地率统计（绿地面积/行政区面积）：面积分母义务。

- 任务族：`administrative_statistic`, `vegetation_index`, `spatial_equity`
- 主制图：`administrative_choropleth`
- 核心能力：`raster_source`, `ndvi`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）, `denominator`*（block）（`*` = 必选）
- 科学义务：`greening_rate_denominator`（denominator → GREENING_RATE_MISSING_AREA）
- 语义回退：`DATA_ROLE_MISSING_DENOMINATOR` → green_area_stats（degraded）
- 路由关键词：绿化率、绿地率、人均绿地、greening rate
- 内容指纹：`48a9a001169b2003…`

### `landuse_structure_admin` — 用地结构统计

行政区用地/功能区结构统计（各类面积占比）：口径与年代披露。

- 任务族：`administrative_statistic`, `categorical_distribution`, `raster_distribution`
- 主制图：`administrative_choropleth`；辅：`categorical_thematic`
- 核心能力：`raster_source`, `raster_reclassify`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`, `category_breakdown`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：用地结构、用地构成、功能区统计、land use structure
- 内容指纹：`acb09bb65a0d6742…`

### `nightlight_vitality_profile` — 夜间灯光活力画像

夜间灯光/亮度栅格的城市活力画像（代理指标）：影像时相强披露。

- 任务族：`raster_distribution`, `temporal_trend`
- 主制图：`raster_surface`；辅：`administrative_aggregation`
- 核心能力：`raster_source`, `zonal_statistics`, `admin_boundary_query`, `admin_aggregation`, `temporal_profile`
- 数据角色：`subject`*（block）, `boundary`（`*` = 必选）
- 科学义务：`nightlight_proxy_semantics`（disclosure → NIGHTLIGHT_PROXY_SEMANTICS）
- 路由关键词：夜间灯光、城市活力、灯光强度、nighttime lights、urban vitality
- 内容指纹：`c9eb5c9ef79f4f24…`

### `poi_function_mix` — POI 功能混合度

街区 POI 功能混合度画像（类别熵代理）：代理语义披露。

- 任务族：`categorical_distribution`, `administrative_statistic`
- 主制图：`administrative_choropleth`；辅：`aggregate_grid`
- 核心能力：`poi_query`, `category_breakdown`, `grid_binning`, `admin_boundary_query`, `admin_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 科学义务：`mix_proxy_semantics`（disclosure → FUNCTION_MIX_PROXY_SEMANTICS）
- 路由关键词：功能混合、业态混合、职住混合、活力多样性、function mix、poi diversity
- 内容指纹：`b5ae69485367a3d6…`

### `urban_service_density_profile` — 城区服务密度画像

城区各类服务设施密度分级画像（格网/行政区双尺度）。

- 任务族：`analytical_density`, `administrative_statistic`, `distribution_overview`
- 主制图：`aggregate_grid`；辅：`administrative_choropleth`
- 核心能力：`poi_query`, `grid_binning`, `admin_boundary_query`, `admin_aggregation`, `rate_aggregation`
- 数据角色：`subject`*（block）, `boundary`*（block）（`*` = 必选）
- 路由关键词：服务密度、设施密度画像、城区密度、urban service density
- 内容指纹：`9071025a56388ca3…`

