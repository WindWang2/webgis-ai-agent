# 算法目录（自动生成 · ADR-0099 §63）

> **本文件由注册表生成，请勿手工编辑。** 事实源：
> `app/lib/gis/algorithms/`（算法）、`app/lib/gis/capabilities/`（能力）、
> 各域包 `PARAMETER_CONTRACTS`（参数契约）。
> 再生成：`python scripts/gen_science_catalog.py`。

统计：64 能力 · 88 算法 · 22 参数契约。

## `accessibility` — 网络可达性

需求点对设施集合的可达性指标计算（15 分钟生活圈等）。

- **`network.accessibility`** 网络可达性（`native`·成熟度 已验证，出处: `luo_qi2009`）
  - 假设：2SFCA: 供给/需求两步浮动捕获 —— 第一步 R_j=容量_j/catchment 内需求权重和，第二步 A_i=Σ(cutoff 内 R_j)；15min_circle 法：需求点在 cutoff 内可达任一设施即计入 served（0/1 覆盖，非 2SFCA）；可达性以路网行程时间（分钟）度量，cutoff_minutes 为浮动捕获半径
  - 局限：容量/需求比值代理；E2SFCA 距离衰减未实现（cutoff 内等权）；容量缺省 1.0：未提供 capacity 字段时 R_j 退化为供需计数比；供需完全不可达的需求点计入 unserved（显式），score=0 的解释依赖供需总量披露

## `admin_aggregation` — 行政区聚合统计

点落入面聚合（各区数量）。

- **`spatial.aggregate.admin`** 点落入面聚合（行政区统计）（`native`·成熟度 已验证）
  - 假设：空间连接谓词 intersects（边界点计入其贴边多边形，聚合约定）；无点多边形 count=0 且 has_data=False；真 0 与无数据显式区分（#693）；点/面 CRS 不一致时先统一到 UTM 工作帧再连接
  - 局限：count 聚合非密度/率——归一化需显式分母（见 spatial.aggregate.rates）

## `admin_boundary_query` — 行政区边界获取

获取行政区边界面（本地 SHP 优先）。

- **`admin.boundary.local`** 行政区边界获取（本地 SHP）（`native`·成熟度 —）
- **`admin.boundary_lookup`** 行政区边界获取（`native`·成熟度 —）

## `analytical_density` — 分析密度

定量密度（每平方公里密度等）——拒绝把视觉热力当定量结果。

- **`density.analytical.mixed`** 分析密度（KDE/聚合混合路径）（`native`·成熟度 —）

## `band_math` — 波段/栅格代数

逐像元栅格代数（A/B 表达式、常数运算；A 为基准网格，B 自动对齐）。

- **`raster.algebra`** 栅格计算器（窗口化）（`native`·成熟度 —）

## `category_breakdown` — 类别构成统计

按类别字段统计构成。

- **`stats.category.breakdown`** 类别构成统计（`native`·成熟度 —）

## `change_detection` — 时序要素变化检测

矢量要素的双时相对比变化集（栅格图像变化用 raster_change_detection）。

- **`temporal.change`** 时序变化（`native`·成熟度 —）

## `closest_facility` — 最近设施

从需求点到设施集合的 top-K 最近路径。

- **`network.closest_facility`** 最近设施（`native`·成熟度 已验证，出处: `dijkstra1959`）
  - 假设：所有 需求×设施 对的代价来自同一棵逐起点 Dijkstra 最短路树（#489），选 K 近仅在代价上排序；travel_direction 决定方向性：incident_to_facility（需求→设施）或 facility_to_incident；零代价匹配（需求点恰在设施处）是合法匹配（#456）
  - 局限：网络不连通/超出 cutoff 的需求点不产路线，逐一点列入 summary.unmatched_demand_ids（不静默丢弃）；OD 树代价不含转向惩罚（树无路径上下文，#455 跨工具语义）
  - 回退：`network.shortest_path`→approximation

## `convex_hull` — 凸包

点/面要素集的最小凸包围合多边形。

- **`geometry.convex_hull`** 凸包（`native`·成熟度 已验证）
  - 假设：UTM 投影平面上的最小凸包（GEOS convex_hull），结果回 WGS84；group_by 给定时按属性分组各建一个凸包
  - 局限：<3 个非共线要素的组/集合退化为 Point/LineString —— 诚实拒绝不产出假多边形；度空间共线的点在 UTM 投影后可变成极薄三角形（投影非仿射），不保证仍失败

## `density_surface` — 视觉密度面

视觉热力（回答『大概哪儿密』，非定量）。

- **`density.visual.heatmap`** 视觉热力（渲染态密度）（`native`·成熟度 —）

## `external_route_planning` — 外部路径规划

经外部服务商（高德/百度）API 的点对点路径规划（驾车/步行/骑行/公交），返回距离、耗时与路线坐标；依赖服务商 API Key。

- **`network.route_external_api`** 外部路径规划（高德/百度）（`native`·成熟度 实验）
  - 假设：路线/距离/耗时完全由服务商（高德或百度）路径规划 API 给出，本地不做路网构图；输入为 WGS84 [lng,lat]，由服务商做坐标与路况语义解释
  - 局限：外部依赖：需 AMAP_API_KEY 或 BAIDU_API_KEY；配额/可达性/口径随服务商；结果含 fetched_at 戳：实时路况敏感，逐次调用不可复现（deterministic=False）；与服务商计费口径一致的路线不与本地路网分析（network.shortest_path）互相 fallback

## `general_g` — Getis-Ord General G

全局高值聚集检验（非负值；高值/低值聚集判别）。

- **`stats.general_g`** Getis-Ord General G（全局高值聚集）（`native`·成熟度 已验证，契约: `general_g_analysis`，出处: `ord_getis1995`）
  - 假设：G=Σ_{i≠j} w_ij·x_i·x_j / Σ_{i≠j} x_i·x_j，二值距离阈值权重；值必须非负（计数/强度语义）；负值拒绝；距离阈值缺省按 8 近邻平均距离自动（E-7 规则）
  - 局限：G 显著偏低=低值聚集（clustered-low），不是『高值聚集』的镜像陈述；G 只检验高值聚集，不能定位热点（定位用 hotspot_analysis/h3_lisa）；非负约束使 General G 不适用于中心化/标准化变量

## `geometry_buffer` — 几何缓冲

点/线/面缓冲几何。

- **`geometry.buffer`** 几何缓冲（`native`·成熟度 已验证，契约: `buffer_analysis`）
  - 假设：UTM 自动投影后米制缓冲，结果回 WGS84；缓冲距离按输入 unit（m/km）换算为米后在 UTM 平面应用；已投影输入保持原 CRS：非米制线性单位（英尺等）按轴因子换算（#524/#588）
  - 局限：UTM 带内大地测量尺度误差 <0.1%（跨带/大范围数据失真增大）；quad_segs 圆弧离散化使点缓冲面积略小于 πr²（~0.16%，golden G1 容差 1%）

## `geometry_centroid` — 几何中心统计

图层量纲摘要中的质心/平均中心（并集质心或显式 mean_center）。

- **`geometry.center_statistics`** 几何中心统计（`native`·成熟度 实验）
  - 假设：spatial_stats 的 centroid = 并集几何质心（UTM 下计算后回 WGS84）；点集时等价于无权平均中心；面集时近似面积加权质心；central_feature 提供显式 mean_center / central_feature 两种口径
  - 局限：并集质心不是加权平均中心：需要显式加权中心用 central_feature；spatial_stats 是量纲摘要（total_area_m2/total_length_m/bbox/centroid），不是中心趋势的显著性描述（离散度用 standard_deviational_ellipse）

## `geometry_clip` — 几何裁剪

要素裁剪。

- **`geometry.clip`** 几何裁剪（`native`·成熟度 —）

## `geometry_dissolve` — 融合/溶解

同属性面融合。

- **`geometry.dissolve`** 融合溶解（`native`·成熟度 —）

## `geometry_overlay` — 几何叠加

GEOS 拓扑叠加（intersection/union/difference 等），纯拓扑不量度。

- **`geometry.overlay`** 几何叠加（`native`·成熟度 已验证）
  - 假设：GEOS 精确拓扑叠加（intersection/union/difference/symmetric_difference/identity）；叠加在 WGS84 工作帧执行：图层 CRS 不一致时先对齐到 layer_a；结果属性 = 两图层属性列的并集（gpd.overlay 语义）
  - 局限：纯拓扑运算：叠加输出坐标仍是度，叠加面积须另投影后量测；输入几何经 make_valid 修复（无效多边形可能改变边界形状）；面×点叠加结果是点集（输出按 polygon_feature_set 声明以面×面为主）

## `getis_ord_gi_star` — Getis-Ord Gi*

热点显著性 Gi*。

- **`stats.h3_hotspot`** H3 Gi* 热点（`native`·成熟度 已验证，出处: `getis_ord1992`, `benjamini_hochberg1995`）
  - 假设：Gi* 含 w_ii=1（distance band 内二值权重，含自身）；p 值为正态近似（非置换）；q_value_fdr 为 BH-FDR 校正（G-6/#870）
  - 局限：正态近似在小样本/偏态分布下 p 值偏乐观；逐格检验的多重比较问题由 BH-FDR 缓解而非消除

## `global_gearys_c` — 全局 Geary 指数

全局空间自相关检验（成对差版本，对局部差异更敏感）。

- **`stats.gearys_c`** 全局 Geary 指数（`native`·成熟度 已验证，契约: `geary_c_analysis`，出处: `geary1954`）
  - 假设：C=(n-1)·Σw_ij(x_i-x_j)²/(2·S0·Σz²)，行标准化权重；置换检验与 Moran 同策略：固定种子 42、双侧 +1 校正；与 Moran 的 I 相比 C 对局部差异更敏感（成对差而非叉积）
  - 局限：checkerboard 完美负自相关的 C 上限是 2-2/n（非精确 2）；99 次置换的 p 值分辨率只有 1/100；解析方差（analytic_variance）依赖正态假设，偏态数据失真

## `global_morans_i` — 全局莫兰指数

全局空间自相关检验。

- **`stats.morans_i`** 全局莫兰指数（`native`·成熟度 已验证，契约: `moran_i_analysis`，出处: `moran1950`, `benjamini_hochberg1995`）
  - 假设：默认 KNN k=8 二值权重，对称并集 + 行标准化（#1002 语义）；置换检验 99 次、固定种子 42、双侧 (count+1)/(perms+1)；地理输入自动投影到局部 UTM 后建权重
  - 局限：KNN 权重对面数据只是邻接的近似（queen/rook 更贴切）；99 次置换的 p 值分辨率只有 1/100（可升 199/499/999）；自动 UTM 对跨带数据有投影失真

## `grid_binning` — 格网聚合

点聚合入 H3 六边形/渔网格网。

- **`spatial.grid.h3`** H3 六边形聚合（`native`·成熟度 —）
  - 回退：`spatial.grid.fishnet`→approximation
- **`spatial.grid.fishnet`** 渔网格网聚合（`native`·成熟度 —）
  - 回退：`spatial.grid.h3`→approximation

## `hotspot` — 热点显著性分析

Getis-Ord Gi* 等空间聚类显著性检验。

- **`spatial.hotspot.local`** 局部热点显著性（Getis-Ord Gi*）（`native`·成熟度 —）

## `kde_density` — 核密度估计

KDE 连续密度面/等值线（定量密度表达）。

- **`spatial.kde.contours`** 核密度等值线（`native`·成熟度 —）
  - 回退：`spatial.kde.surface`→equivalent
- **`spatial.kde.surface`** 核密度全格网表面（`native`·成熟度 —）
  - 回退：`spatial.kde.contours`→equivalent

## `local_morans_i` — 局部莫兰/LISA

局部热点/冷点聚类。

- **`stats.h3_lisa`** H3 LISA 局部自相关（`native`·成熟度 已验证，出处: `anselin1995`）
  - 假设：esda.Moran_Local（Queen 邻接、行标准化、seed=42）；孤岛格网给中性结果（p=1、q=0），保持行对齐（#927）；输入为带数值字段的 H3 网格（如 h3_binning 产物）
  - 局限：逐格 p_sim<0.05 在随机数据下期望产出 ~0.05n 假显著（结果内披露期望数）；H3 分辨率改变邻接结构，跨分辨率结果不可比

## `location_allocation` — 区位配置

设施选址-分配优化（tier-3 门控）。

- **`network.location_allocation`** 区位配置（`native`·成熟度 已验证，出处: `teitz_bart1968`）
  - 假设：p_median 目标 = 最小化 Σ w_i·min_{j∈S} C_ij；max_coverage = 最大化 cutoff 内覆盖需求权重；代价矩阵 = 路网 OD 行程时间（不可达 = inf，参与目标时按 1e9 惩罚）
  - 局限：启发式 >20k 组合；exact ≤20k —— C(m,p) 枚举在预算内给出精确最优，超出切 Teitz-Bart 顶点替换 / 贪婪覆盖（近优非最优，summary.solver 披露）；不可达需求点列入 summary.unassigned_ids（不参与选址目标）；Teitz-Bart 收敛依赖初始化（前 p 个候选），无多起点重启

## `mcda_evaluation` — 多准则决策评价

候选方案×准则×约束的 MCDA 评价（WSM/TOPSIS + Pareto + 敏感性）。

- **`decision.mcda.wsm`** MCDA 决策评价（WSM/TOPSIS）（`native`·成熟度 已验证，出处: `hwang_yoon1981`）
  - 假设：权重/准则方向由声明给定；蒙特卡洛不确定性仅在声明不确定参数时激活
  - 局限：不合成证据：无不确定参数时不注入伪噪声分布

## `multi_ring_buffer` — 多环缓冲

同心多距离环/环带（band 互斥、并集覆盖最大盘）。

- **`geometry.multi_ring_buffer`** 多环缓冲（`native`·成熟度 已验证）
  - 假设：UTM 投影平面米制缓冲；升序距离环，merge_rings=True 时内环被外环差集扣除；环带宽度 = 相邻距离差（band i 覆盖 (d_{i-1}, d_i]）
  - 局限：UTM 带内大地测量尺度误差 <0.1%（同 geometry.buffer）；quad_segs=32 圆弧离散化使环面积与解析环差 ~0.1%；非米制已投影输入按轴因子换算（#588），极小负/零距离拒绝

## `ndvi` — NDVI 植被指数

遥感 NDVI 计算。

- **`remote.ndvi`** NDVI 植被指数（`native`·成熟度 已验证，出处: `rouse1974`）
  - 假设：反射率需 0-1 定标；零分母→NaN（nodata 像元不稀释统计）；在线路径按 STAC 波段语义取 B04/B08（显式角色映射，非位置猜测）
  - 局限：比值指数对线性缩放不变，但对云影/气溶胶/定标漂移敏感；无大气校正补偿，跨期可比性依赖同一 L2A 产品线

## `od_flow_mapping` — OD 流向图

把 OD 对（坐标+权重）构建为有界流向线要素层。

- **`flow.od_arc_build`** OD 流向构建（`native`·成熟度 —）

## `od_matrix` — OD 成本矩阵

多起点×终点网络成本矩阵。

- **`network.od_matrix`** OD 成本矩阵（`native`·成熟度 已验证，契约: `network_od_matrix`，出处: `dijkstra1959`）
  - 假设：每个唯一起点一趟累积式 Dijkstra（#449），距离/时间沿同一最短路树累积（GIS-19）；cutoff_s 以活动阻抗为单位（秒/米）；超出预算的对以 reachable=False + inf 返回，绝不静默缺行；有向图语义：单行路网下 OD(A→B) ≠ OD(B→A)
  - 局限：OD 树代价不含转向惩罚（树无路径上下文，#455 跨工具语义）；起点/终点捕捉在 500 m 容差内静默吸附最近边；捕捉距离在结果 snap_evidence 中逐端点披露

## `poi_query` — POI 要素获取

按范围/类别获取点要素（本地优先，在线兜底）。

- **`poi.query.local`** POI 查询（本地优先）（`native`·成熟度 —）
- **`poi.area_search`** 区域 POI 检索（`native`·成熟度 —）

## `point_pattern_analysis` — 点格局分析

点格局统计（Ripley K / 样方 χ² / NNI / 密度聚类）——回答『点的空间分布是聚集/均匀/随机』，与密度面表达正交。

- **`point_pattern.quadrat_test`** 样方 χ² 离散检验（`native`·成熟度 已验证，契约: `quadrat_analysis`）
  - 假设：期望频数 N/(mn)；χ² 检验 df=mn-1；样方划分覆盖数据 bbox（工具层自动 UTM 投影后划分）；VMR（方差/均值比）>1 聚集、<1 均匀
  - 局限：对网格粒度敏感（粒度变→结论可变），建议多粒度对照；期望频数<5 时 χ² 近似变差（结果内 chi2_approx_warning 披露）；bbox 自适应窗口会把『集中在一角』归一化掉（lib 支持 fixed window）
- **`point_pattern.ripley_k`** Ripley's K 函数（`native`·成熟度 已验证，契约: `ripley_k_analysis`，出处: `ripley1976`）
  - 假设：同质（CSR 可作参考）二阶结构；各向同性边缘校正（矩形窗）；K(r)=A/(n(n-1))·Σ I(d≤r)/w_ij，w_ij 为圆周入窗比例；r_max=max_distance_ratio×min(窗宽,窗高)，≤0.5 保边缘校正可信
  - 局限：描述性输出（无显著性 p 值）；显著性需固定种子 CSR 模拟包络；O(n²) 成对统计，上限 2 万点（超出诚实拒绝）；非矩形研究域的边缘校正按外接矩形近似
- **`point_pattern.dbscan`** DBSCAN 密度聚类（`native`·成熟度 已验证，出处: `ester_kriegel1996`）
  - 假设：eps（米）/min_samples 定义密度可达；地理输入自动投影 UTM；无值维时纯空间聚类；value_field 时值维按坐标 σ 缩放（#867）
  - 局限：eps 对结果高度敏感且无自动选择；密度不均的数据单一 eps 会把稀疏簇判为噪声
- **`point_pattern.nni`** 最近邻指数（NNI）（`native`·成熟度 实验，出处: `clark_evans1954`）
  - 假设：R=观测最近邻均值/CSR 期望（0.5·√(A/N)，A 取 bbox）；R<0.7 聚集 / >1.3 分散的阈值为经验分档（非检验）；地理输入自动投影到局部 UTM
  - 局限：R 阈值无显著性检验（p 值未实现）；bbox 面积作 CSR 期望，窗形偏离矩形时期望偏

## `point_profile` — 数据画像

点数/几何/字段画像（不产出新数据，产出元数据）。

- **`profile.spatial.stats`** 空间数据画像（`native`·成熟度 —）

## `proximity_buffer` — 邻近缓冲

距离缓冲区生成。

- **`spatial.buffer.proximity`** 距离缓冲区（`native`·成熟度 —）

## `raster_change_detection` — 双时相栅格变化检测

两个栅格工件的对齐像元级变化检测（差值/绝对差/归一化差 + 阈值分类）。

- **`remote.change.raster`** 双时相栅格变化检测（`native`·成熟度 —）
  - 假设：A（T1）网格为基准，B 经 WarpedVRT 对齐；对齐事实进质量证据；有效像元 = 双方都有效（任一 nodata → nodata）
  - 局限：差值法对配准/辐射差异敏感，无语义分类（变化≠地类转移）；normalized_difference 零分母 → nodata（不产 inf）
- **`remote.cva`** 变化向量分析（CVA）（`native`·成熟度 已验证，出处: `malila1980`）
  - 假设：两景波段按语义角色对齐（缺角色拒绝，不按位置猜测）；幅度=全角色欧氏范数；角度=固定角色序前两分量 atan2（弧度）；同一像元任一角色任一期无效 → 输出 NaN
  - 局限：CVA 只给幅度/方向，不构成土地覆盖语义变化；方向角依赖角色序约定——跨研究比较需披露所用角色序
- **`remote.ratio_change`** 双时相比值变化（`native`·成熟度 已验证，契约: `ratio_change_analysis`）
  - 假设：比值法适用于 SAR 后向散射/强度（同量纲输入）；ratio：a/b，零分母→NaN；log_ratio：log(a)−log(b)（对数域对称）
  - 局限：比值不区分变化原因（物候/几何/定标漂移同权混合）；log_ratio 输入须为正（线性强度或 dB）

## `raster_reclassify` — 栅格重分类

连续栅格值按方案映射为离散类别。

- **`raster.reclassify.rule`** 规则重分类（`native`·成熟度 —）

## `raster_resample` — 栅格重采样

改变像元大小和/或 CRS（对齐预处理）。

- **`raster.resample.grid`** 网格重采样/重投影（`native`·成熟度 —）

## `raster_source` — 栅格数据源

DEM/遥感栅格获取。

- **`raster.source.dem`** DEM 栅格获取（`native`·成熟度 —）

## `rate_aggregation` — 率/密度聚合

显式分母的逐区归一化：分子（字段求和/计数）÷ 分母（区字段/真实面积/要素计数）；count 聚合不是率/密度，分母缺失/≤0 的区不产率值（rate=null）。

- **`spatial.aggregate.rates`** 显式分母聚合（率/密度）（`native`·成熟度 实验，契约: `aggregate_with_denominator`）
  - 假设：分子 = 分子字段按区求和（NaN 值剔除并披露）或缺省的要素计数；分母三种口径：区分母字段（field）/ 区真实面积 m²（area）/ 要素计数（count）；率 = 分子 ÷ 分母；面积分母在 UTM/极方位度量 CRS 下计算（Web Mercator 不可信）
  - 局限：分母通道已接入 spatial_aggregate 工具（denominator_kind/numerator_field/denominator）——需中央接线 numerator_field/denominator_kind/denominator_field 三个参数；count 分母的输出是比值（count_ratio_not_rate），不是率/密度；分母缺失/≤0 的区 rate=None（JSON null）——从不编造 0 或 inf

## `route_optimization` — 路线优化

多站点访问顺序优化（VRP，tier-3 门控）。

- **`network.route_optimization`** 路线优化（`native`·成熟度 已验证）
  - 假设：最近邻初始巡游 + 2-opt 局部搜索改进（有向代价矩阵，方向翻转计价 #540）；leg 代价 = 活动阻抗下的路网最短路（OD 树重建，无逐 leg 重复寻路）
  - 局限：NN+2-opt 启发式非精确 TSP：解无最优性保证（迭代上限 100）；不可达 leg 计 1e9 代价（巡游仍连贯，总代价如实累加 inf leg）
- **`network.optimize_route`** 路线优化（VRP）（`native`·成熟度 已验证）
  - 假设：最近邻初始巡游 + 2-opt 局部搜索改进（有向代价矩阵，方向翻转计价 #540）；leg 代价 = 活动阻抗下的路网最短路（OD 树重建）
  - 局限：NN+2-opt 启发式非精确 TSP：解无最优性保证（迭代上限 100）；stops 上限 200（工具层显式拒绝超限，2-opt 超线性）

## `sar_analysis` — SAR 时序/极化分析

SAR 时序栈统计、VV/VH 极化比与双时相对数比值（无斑点滤波/无辐射定标的诚实边界）。

- **`sar.temporal_stats`** SAR 时序栈统计（`native`·成熟度 已验证，契约: `sar_temporal_stats_analysis`）
  - 假设：输入假定已几何校正并对齐；std 为总体标准差（ddof=0）；nodata/NaN 逐切片剔除，剩余有效切片上统计（部分有效像元披露）
  - 局限：无斑点滤波、无辐射定标（对应能力为 planned，见 sar.speckle_filter/sar.radiometric_calibration）；栈深 ≤24、H·W ≤4096×4096，超限 ResourceScaleMismatch 先拒绝
- **`sar.vh_ratio`** SAR VV/VH 极化比（`native`·成熟度 已验证）
  - 假设：VV/VH：线性域为比值、dB 域为 dB 差（VV−VH）；VH=0 → NaN；同景双极化（如 Sentinel-1 VV+VH）
  - 局限：无辐射定标假定下仅作结构对比代理，非物理量
- **`sar.log_ratio_change`** SAR 双时相对数比值变化（`native`·成熟度 已验证）
  - 假设：log(a)−log(b)：对数域对称（增强=衰减镜像），SAR 双期惯用量；经 detect_ratio_change 工具 method=log_ratio 参数执行
  - 局限：比值不区分变化原因；输入须为正（线性强度或 dB）

## `sar_radiometric_calibration` — SAR 辐射定标

DN → σ⁰/γ⁰ 辐射定标——未实现，planned。

- **`sar.radiometric_calibration`** SAR 辐射定标（`planned`·成熟度 —）
  - 假设：DN → σ⁰/γ⁰（需定标常数与参考面）
  - 局限：未实现——planned 条目；比值法在同量纲输入下部分免疫

## `sar_speckle_filtering` — SAR 斑点滤波

SAR 相干斑点噪声抑制（Lee/Lee-Sigma 家族）——未实现，planned。

- **`sar.speckle_filter`** SAR 斑点噪声滤波（`planned`·成熟度 —）
  - 假设：斑点为乘性噪声（Lee/Lee-Sigma/Refined-Lee 家族假设）
  - 局限：未实现——planned 条目；现网 SAR 统计假定未滤波输入

## `service_area` — 网络服务区

等时圈/网络可达服务区。

- **`network.isochrone`** 网络等时圈（`native`·成熟度 实验）
  - 假设：外部高德路径规划 API 沿路网采样近似等时圈；mode 速度表：walking 80 / cycling 250 / driving 667 / transit 417 m/min
  - 局限：依赖外部 AMAP_API_KEY 与服务商可用性（结果含 fetched_at 戳）；本地路网等时圈用 network.service_area.multi（network_service_area / isochrone_network）；等时圈形态由服务商语义决定，与本地路网构图结果可不同
- **`network.service_area.simple`** 简化服务区（速度表缓冲）（`native`·成熟度 实验）
  - 假设：距离 = 速度表[mode] × travel_time_min 的直线（欧氏）缓冲：walking 5 / cycling 15 / driving 40 km/h；不做路网构图、不解析拓扑 —— 输出是设施点的等距圆，非沿路可达范围
  - 局限：接近性代理（proxy）：速度表×时间的直线（欧氏）缓冲，忽略路网拓扑/单行线/障碍/河流分隔，实际路网可达范围可显著小于缓冲圈；跨水系/高架隔断的区域会严重高估覆盖（用 network.isochrone / network.service_area.multi 做真实路网等时圈）；速度为模式级常数，不含拥堵与路况
  - 回退：`network.isochrone`→proxy
- **`network.service_area.multi`** 多断点服务区（`native`·成熟度 已验证，契约: `network_service_area`，出处: `dijkstra1959`）
  - 假设：有向图 Dijkstra 可达集（respect 单行线/障碍），逐 break 分类可达边并按剩余预算截断部分边（#618-20）；break 单位 minutes/meters/seconds（km 为米别名）；minutes 断点按墙钟时间换算（#618-20/#706）；边界多边形 = 可达边在局部 UTM 的固定米半径缓冲并集（GIS-08/09，不桥接不可达缝隙）
  - 局限：等时圈多边形是可达路网的 150 m 平滑缓冲包络，不是精确步行/车行边界；设施捕捉节点不在图内时该设施不产出服务区，id 在结果 summary.unreachable_facility_ids 中披露；无投影（极区）退化为纬度校正的点缓冲 fallback（GIS-08）
- **`network.isochrone.local`** 本地路网等时圈（`native`·成熟度 已验证）
  - 假设：输入路网线要素（调用方提供）建无向 MultiGraph，按 mode 速度×时间预算做 Dijkstra 可达集；边长在局部 UTM 度量（to_utm_gdf 自动投影，GIS-02 同源语义）；设施投影到最近边后从两端点种子；mode 速度表：walking 80 / cycling 250 / driving 667 / transit 417 m/min
  - 局限：无向图语义：单行线/转向限制不生效（需有向语义用 network.service_area.multi）；单一时间断点（travel_time），不支持多 break 嵌套输出；路网数据需调用方提供；空路网返回结构化失败（不静默空圈）

## `shortest_path` — 最短路径

网络最短路径。

- **`network.shortest_path`** 最短路径（`native`·成熟度 已验证，契约: `network_shortest_path`，出处: `dijkstra1959`）
  - 假设：边权 = length_m（haversine 测段长）或 travel_time_s（长度/属性速度），Dijkstra/A* 在有向图上最优；A* 启发式 = haversine直线距 × 图内最小每米成本（对任意阻抗可采，#447）；坐标端点自动捕捉到最近边并插入虚拟节点（GIS-01），路线真正起止于捕捉点
  - 局限：端点捕捉容差默认 500 m：超容差捕捉 confidence=0 并在结果警告中披露（不拒绝请求）；图不连通时返回 total_cost=inf 的空路线（origin/destination 保留），不静默以欧氏距离替代路网距离；边长为 haversine（测地）近似，无高程/坡度阻抗

## `spatial_interpolation` — 空间插值

IDW / Kriging 等插值。

- **`interpolation.idw`** IDW 插值（`native`·成熟度 已验证，契约: `idw_interpolation`，出处: `shepard1968`）
  - 假设：精确插值器（过样本点）；无理论方差——不确定性以 LOOCV 残差证据呈现；米制距离：地理输入经 estimate_utm_crs 自动投影（极区用极方位立体投影）；k=5 最近邻截断（与主路径一致）；重复坐标先按均值聚合（确定性）
  - 局限：跨带数据自动 UTM 有投影失真（单带处理，无跨带拆分）；LOOCV 残差分位数是样本内证据，不外推为置信区间；样本凸包外的外推由幂次主导，远端值趋向邻域均值
  - 回退：`interpolation.kriging`→equivalent
- **`interpolation.rbf`** RBF 径向基插值（`native`·成熟度 已验证，契约: `rbf_interpolation`）
  - 假设：scipy RBFInterpolator：核薄板样条默认，smoothing=0 时精确过样本点；米制距离：地理输入经 estimate_utm_crs 自动投影（与 IDW 同一 CRS 政策）；局部 RBF（neighbors ≤64）：超样本数时按 KdTree 最近邻截断
  - 局限：多二次/高斯类核在大数据集上病态（本实现未含 gaussian 核）；>2 万点确定性行距抽稀（metadata.disclosures 披露），>10 万点拒绝；外推区域行为由核多项式项主导，远端可能发散（无钳制）
  - 回退：`interpolation.idw`→approximation
- **`interpolation.kriging`** 普通克里金插值（`native`·成熟度 生产，契约: `kriging_interpolation`，出处: `matheron1963`）
  - 假设：二阶平稳性假设：变异函数从数据估计（加权 RSS 最低的模型胜出）；规范半方差构造（Isaaks & Srivastava）：nugget 进所有 h>0 项与 γ₀，对角为零；k 邻域（≤24）系统分批求解；高斯模型加 ridge 稳定化，退化逐格计数
  - 局限：EPSG:3857 被接受为工作 CRS 但含 Web Mercator 尺度畸变（高纬非真实地面距离）；趋势明显的场 OK 有系统偏差——改用 interpolation.universal_kriging；变异函数拟合失败 / 滞后 bin 不足时结构化拒绝（不静默降级）
  - 回退：`interpolation.idw`→approximation
- **`interpolation.universal_kriging`** 泛克里金插值（`native`·成熟度 已验证，契约: `kriging_interpolation`，出处: `matheron1963`）
  - 假设：线性漂移 E[Z(x)]=b0+b1·x+b2·y；变异函数在 OLS 去趋势残差上拟合；UK 系统带趋势约束 Lagrange 乘子；方差 = wᵗγ₀ + mᵗf0；零残差退化（数据严格线性）→ 精确趋势预测、方差 0、披露 zero_residual_variance
  - 局限：漂移阶数固定为线性（二次及以上趋势未实现）；EPSG:3857 被接受为工作 CRS 但含 Web Mercator 尺度畸变（与 OK 同）；样本 <12 拒绝（InsufficientSamples）；普通克里金 ≥8 即可
  - 回退：`interpolation.kriging`→approximation

## `spatial_join` — 空间连接

按拓扑关系把右表属性挂到左表（区别于几何裁剪）。

- **`geometry.spatial_join`** 空间连接（`native`·成熟度 —）

## `spatiotemporal_clustering` — 时空聚类

ST-DBSCAN 等时空聚类（与 LISA 局部自相关是不同检验）。

- **`temporal.hotspot`** 时空热点（`native`·成熟度 —）
- **`stats.st_dbscan`** 时空 DBSCAN 聚类（`native`·成熟度 —）

## `spectral_index` — 类型化光谱指数

按语义角色（red/nir/swir1/...）显式命名的 12 公式族光谱指数（含出处与值域诚实报告）。

- **`remote.spectral_index`** 类型化光谱指数（12 公式族）（`native`·成熟度 已验证，契约: `spectral_index_analysis`，出处: `rouse1974`, `huete1988`, `gao1996`, `xu2006`, `zha_woodcock2003`, `key_benson2006`, `mcfeeters1996`）
  - 假设：波段按语义角色显式命名（band_map），绝不按波段位置猜测；线性定标先于公式（DN/10000→反射率）；零分母→NaN；超理论值域只报告不钳制（out_of_range_fraction）
  - 局限：公式出处逐指数声明（gndvi/msavi/ndmi 无词表出处，诚实留空）；EVI/SAVI 常数项只在反射率单位下成立（#382）

## `temporal_aggregate` — 时间聚合

按时间窗重采样汇总。

- **`temporal.aggregate`** 时间聚合（`native`·成熟度 —）

## `temporal_change_point` — 时序均值变点

CUSUM 单均值漂移定位 + 固定种子 bootstrap 显著性（多变点不在模型内）。

- **`temporal.changepoint`** CUSUM 均值变点（`native`·成熟度 已验证，契约: `temporal_changepoint_analysis`）
  - 假设：单均值漂移假设：变点 = argmax|Σ(x−x̄)|（k 取 1..n−1）；显著性 = 无变化零假设下固定种子 bootstrap 的 max-CUSUM 分布；p ≥ alpha 时不给 change_point_index（candidate 恒给）
  - 局限：多变点/方差变化不在模型内；n<10 变点定位不稳定（警告）；bootstrap p 分辨率 1/(draws+1)

## `temporal_profile` — 时间画像

时间字段/跨度/粒度画像（元数据，不产新数据）。

- **`temporal.profile`** 时间画像（`native`·成熟度 —）

## `temporal_trend` — 时序趋势

时间维度的趋势/聚合/时空热点分析。

- **`temporal.trend`** 时序趋势（`native`·成熟度 已验证，契约: `temporal_trend_analysis`，出处: `sen1968`, `mann1945`, `kendall1975`）
  - 假设：缺省 ols_sen：Sen 中位斜率 + OLS，行为与历史逐位一致；MK 族：tie 校正方差 + 连续性校正正态 z + 双侧 p；显著性证据仅在 mann_kendall/seasonal 分支产出（ols_sen 无 p 值）
  - 局限：序列相关（lag-1 秩自相关超限）会夸大 MK 显著性——结果内警告；季节 MK 无预白化（prewhitening 未实现）；观测 <3 的季节跳过并披露；两时间点无法定义趋势统计量（n=2 拒绝，非降级描述）
- **`temporal.raster_ts`** 时序栅格（`native`·成熟度 —）

## `terrain_aspect` — 坡向分析

DEM 坡向。

- **`terrain.aspect`** 坡向（`native`·成熟度 已验证，出处: `horn1981`）
  - 假设：3×3 Horn 梯度；度栅格需 z_factor/纬度修正；坡向 = 下坡方位（顺时针自北 0-360°）；平地 → NaN
  - 局限：平地/近平地坡向数值不稳定（梯度趋于 0）；边界像元 edge 复制延拓（单侧差分）

## `terrain_contours` — 等值线提取

DEM 等值线提取（marching squares → GeoJSON LineString，顶点映射到世界坐标；nodata 断线）。

- **`terrain.contours`** 等值线提取（`native`·成熟度 已验证，契约: `extract_contours`）
  - 假设：marching squares 等值线（matplotlib Agg，无显示环境）；水平选取优先级：显式 levels > interval（自 vmin 等间隔）> n_levels（vmin..vmax 等间隔）；nodata/非有限像元 → NaN 断线；顶点经栅格仿射变换映射到世界坐标
  - 局限：level == 数据极值的退化等值线可能为空（不产要素，meta 披露 levels_drawn）；顶点密度受像元网格限制（无样条平滑/加密）

## `terrain_derivatives` — 地形衍生指标

DEM 邻域地形指标：TPI（Weiss 2001）/TRI（Riley 1999）/粗糙度（Wilson 2007）与平面、剖面曲率（Zevenbergen-Thorne 1987）。

- **`terrain.tpi`** 地形位置指数 TPI（`native`·成熟度 已验证，契约: `terrain_derivative`，出处: `weiss2001`）
  - 假设：TPI = z − 窗口均值（含中心像元）；线性坡面上 ≡ 0；窗口为 3-101 奇数；边界收缩为可得像元（不发明填充值）；与像元尺寸无关（高程同量纲输出）
  - 局限：Weiss 地类分级需双尺度（如 3/25 格）对照，单一窗口不构成分类；积分图均值-平方差在窗口均值远大于离散度时有浮点精度损失
- **`terrain.tri`** 地形崎岖度指数 TRI（`native`·成熟度 已验证，契约: `terrain_derivative`，出处: `wilson2007`）
  - 假设：TRI = sqrt(Σ(z − z_nb)²)，8 个直接邻域（Riley 1999 原式）；边界收缩为可得邻域；平坦面 ≡ 0
  - 局限：只反映 1 像元尺度起伏，不表征多尺度崎岖度；各向异性像元不做距离加权（与 Riley 原式一致的纯差分）
- **`terrain.roughness`** 地形粗糙度（`native`·成熟度 已验证，契约: `terrain_derivative`，出处: `wilson2007`）
  - 假设：粗糙度 = 窗口内高程总体标准差（ddof=0，Wilson 2007 口径）；窗口为 3-101 奇数；边界收缩为可得像元
  - 局限：对离群高程敏感（无稳健尺度）；积分图方差在窗口均值远大于离散度时有浮点精度损失
- **`terrain.curvature`** 平面/剖面曲率（`native`·成熟度 已验证，契约: `terrain_derivative`，出处: `zevenbergen_thorne1987`）
  - 假设：Zevenbergen-Thorne 二阶差分：profile 沿最陡下降方向、plan 沿等高线方向；单位 z_units·cell⁻²（惯例 ×100 报告；元数据披露）；符号约定：profile>0 凸（水流减速）/ plan>0 分散；z=x² 检验 profile=+2、plan=0
  - 局限：3×3 模板对噪声敏感（无预平滑）；边界像元 edge 复制延拓退化为单侧差分

## `terrain_hillshade` — 山体阴影

DEM 山体阴影。

- **`terrain.hillshade`** 山体阴影（`native`·成熟度 已验证，出处: `horn1981`）
  - 假设：3×3 Horn 梯度；度栅格需 z_factor/纬度修正；罗盘方位光照模型：照度 = sin(alt)cos(θ) + cos(alt)sin(θ)cos(az − aspect)
  - 局限：无次级散射/大气效应（朗伯面近似）；边界像元 edge 复制延拓（单侧差分）

## `terrain_hydrology` — D8 水文分析

D8 单向流流向（ESRI 2 的幂编码）、拓扑序汇流累积与逆 D8 上游流域圈定（平地/洼地为汇，不填洼）。

- **`terrain.flow`** D8 流向与汇流累积（`native`·成熟度 已验证，契约: `flow_analysis`，出处: `tarboton1997`）
  - 假设：D8 单向流（ESRI 2 的幂编码 1=E…128=NE；0=sink/outlet）；最陡下降按米制像元距离（地理栅格 x 向 cos(lat)）；并列最陡取最低索引邻域；汇流累积 = 上游贡献像元数（不含自身；全流域出口 = N−1）
  - 局限：D8 单向流限制：格网平行流向偏差，D∞（Tarboton 1997）未实现；平地/洼地即汇（code 0），无 epsilon 梯度平地路由/填洼；流出网格边界的流路终止（boundary = outlet，不外推）
- **`terrain.watershed`** 流域圈定（`native`·成熟度 已验证，出处: `tarboton1997`）
  - 假设：逆 D8 BFS：汇入 pour point 的全部上游像元（含 pour point 自身）；依赖 D8 单向流语义（编码与平局裁决同 terrain.flow）
  - 局限：pour point 不做河道 snap（未对齐河道时流域偏小，由调用方负责）；D8 格网流向偏差会传递到流域边界

## `terrain_slope` — 坡度分析

DEM 坡度。

- **`terrain.slope`** 坡度（`native`·成熟度 已验证，出处: `horn1981`）
  - 假设：3×3 Horn 梯度；度栅格需 z_factor/纬度修正；坡度 = arctan|∇z|（度）；cell_size_x 承接地理栅格 cos(lat) 东西向修正
  - 局限：边界像元 edge 复制延拓（单侧差分）；地理 DEM 未做 cos(lat) 修正时东西向坡度低估 ~cos(lat)；垂直单位非米（英尺 DEM）时需显式 z_factor

## `terrain_viewshed` — 视域分析

DEM 视域：观察点视线遮挡布尔掩膜、可见比例与可见面积（扇区视线角扫描；无地球曲率/大气折射）。

- **`terrain.viewshed`** 视域分析（`native`·成熟度 已验证，契约: `viewshed_analysis`）
  - 假设：无地球曲率/大气折射；目标高度默认 0；扇区视线角判据：目标仰角 ≥ 沿途地形运行最大仰角即可见（切切记可见）；观察点高程 = 观察点地形 + observer_height；射线 ~1 像元 bilinear 采样
  - 局限：扇区角离散 ≈ 最大距离处 1 像元弧长（远距目标近似误差 ≤ 半扇区宽）；观察点邻接 nodata 时高程退化为最近有效像元；地理栅格按 cos(lat) 换算米制像元（带向不修正）

## `traffic_status` — 实时路况

指定矩形/圆形范围内的实时道路拥堵状态查询（拥堵等级+路段长度；外部服务商 API，实时语义、结果不缓存）。

- **`network.traffic_status_external`** 实时路况（高德）（`native`·成熟度 实验）
  - 假设：道路名+拥堵等级+路段长度由高德实时路况 API 给出；矩形或圆形查询范围；拥堵等级：1=畅通 2=缓行 3=拥堵 4=严重拥堵（0=全部）
  - 局限：外部依赖：仅支持高德（需 AMAP_API_KEY）；采样时刻的路况，查询即过期；实时语义显式不缓存（#702）——缓存即错误信息；逐次调用不可复现（deterministic=False）

## `transit_routing` — 公交路径规划

起终点间公交/地铁换乘方案查询（步行段+乘车段，含换乘次数、总耗时、票价；外部服务商 API，当前仅高德）。

- **`network.transit_route_external`** 公交路径规划（高德）（`native`·成熟度 实验）
  - 假设：公交/地铁换乘方案（步行段+乘车段、换乘次数、总耗时、票价）完全由高德 API 给出；city（起点城市）必填；跨城公交需 city_d
  - 局限：外部依赖：仅支持高德（需 AMAP_API_KEY）；策略 0=最快捷/1=最经济/2=最少换乘/3=最少步行/5=不乘地铁；结果含 fetched_at 戳：班次时刻敏感，逐次调用不可复现（deterministic=False）

## `voronoi_tessellation` — Voronoi 剖分

点的 Voronoi/Thiessen 有限区域剖分（镜像外推 + 范围裁剪）。

- **`geometry.voronoi`** Voronoi（Thiessen）剖分（`native`·成熟度 已验证）
  - 假设：scipy Voronoi + 4 轴镜像点外推使边界点获得有限区域；输出按数据范围 +50% 边距裁剪（可用 clip_bounds 显式指定）；每个点格的质心作为剖分种子（非点输入取质心）
  - 局限：边界镜像外推：裁剪框外的区域形状依赖镜像几何，非真实边界；重复点行为：Qhull 退化时诚实报错（QH6154）；一般重复点各得一份相同区域（不去重）；无单元格的退化区域被静默跳过（count < 输入点数）

## `zonal_statistics` — 分区统计

面内栅格 min/max/mean/sum 统计。

- **`remote.zonal_stats`** 分区统计（`native`·成熟度 —）
