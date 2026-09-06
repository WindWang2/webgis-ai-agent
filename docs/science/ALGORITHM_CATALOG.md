# 算法目录（自动生成 · ADR-0099 §63）

> **本文件由注册表生成，请勿手工编辑。** 事实源：
> `app/lib/gis/algorithms/`（算法）、`app/lib/gis/capabilities/`（能力）、
> 各域包 `PARAMETER_CONTRACTS`（参数契约）。
> 再生成：`python scripts/gen_science_catalog.py`。

统计：88 能力 · 128 算法 · 62 参数契约。

## `accessibility` — 网络可达性

需求点对设施集合的可达性指标计算（15 分钟生活圈等）。

- **`network.accessibility`** 网络可达性（`native`·成熟度 已验证，契约: `network_accessibility_analysis`，出处: `luo_qi2009`）
  - 假设：2SFCA: 供给/需求两步浮动捕获 —— 第一步 R_j=容量_j/catchment 内需求权重和，第二步 A_i=Σ(cutoff 内 R_j)；E2SFCA（Foundation V2 A4，Luo & Qi 2009）：cutoff 等分 decay_zones 带，带中点高斯权 w_r=exp(−0.5·(r+0.5)²)；15min_circle 法：需求点在 cutoff 内可达任一设施即计入 served（0/1 覆盖，非 2SFCA）
  - 局限：容量/需求比值代理；2SFCA cutoff 内等权，E2SFCA 按等分带高斯衰减（decay_zones 1-10，缺省 3）——方法即精度权衡；容量缺省 1.0：未提供 capacity 字段时 R_j 退化为供需计数比；供需完全不可达的需求点计入 unserved（显式），score=0 的解释依赖供需总量披露

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

## `bivariate_morans_i` — 双变量 Moran's I

x 与 W·y 的空间共变（Wartenberg 1985；共位相关非因果）。

- **`stats.bivariate_moran`** 双变量 Moran's I（x vs W·y）（`native`·成熟度 已验证，契约: `bivariate_moran_analysis`，出处: `wartenberg1985`, `moran1950`）
  - 假设：I=(n/S0)·Σ x_i(Wy)_i/(‖x-x̄‖·‖y-ȳ‖)，行标准化权重；x=y 时与单变量 Moran 严格一致（属性测试钉住）；置换只打乱 y（固定种子 42，双侧 (count+1)/(perms+1)）
  - 局限：共位相关 ≠ 因果/超前-滞后；方向解读需领域模型支撑；x 与 y 量纲无关（分子分母同除范数），但受离群值影响；与 esda 归一化对齐仅在无 island 权重时成立（S0=n）；含 island 发散 n/S0

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

## `cross_k_function` — 双变量交叉 K 函数

双变量 K12（Besag 1977 随机标记）——两类点间的空间吸引/分离检验（如连锁品牌 vs 竞品共现）。

- **`point_pattern.cross_k`** 双变量交叉 K 函数（`native`·成熟度 已验证，契约: `cross_k_analysis`，出处: `besag1977`, `ripley1976`）
  - 假设：K12(r)=A/(n1·n2)·Σ_{i∈1,j∈2} I(d≤r)/w_ij，w_ij 各向同性逐对校正；随机标记（random labelling）零假设：类型标签在固定位置间置换；置换从池化成对表重抽（非仅观测跨类对），固定种子 42
  - 局限：随机标记只检验『给定位置下的类型关联』，不检验位置格局本身；p 值来自 max|K12−πr²| 秩（+1 校正），分辨率 1/(permutations+1)；O(n²) 成对统计，上限 2 万点（超出诚实拒绝）

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

## `geographical_detector` — 地理探测器

分层解释力 q 统计与双因子交互检测（Wang 2010）。

- **`stats.geodetector`** 地理探测器（因子 q + 交互）（`native`·成熟度 已验证，契约: `geodetector_analysis`，出处: `wang2010`）
  - 假设：q=1-Σ N_h σ_h²/(N σ²)（总体方差），q∈[0,1] 完全分层时 =1；数值分层字段需显式分箱（bins≥2 分位数）；唯一值≤12 按类别；F 检验解析 p；permutations>0 附固定种子 42 的置换 p 与分位
  - 局限：类别交集是两分层的公共加细，q 加细单调不减——weakened 类只在分层被粗化时出现；q 只度量分层解释力，不是因果证据；分层过细（>n/2 层）时 q 退化为 1，被显式拒绝

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

## `gravity_accessibility` — 引力可达性

Hansen 势能模型 A_i=Σ S_j^α/d_ij^β：以路网 OD 成本为距离，输出逐需求点可达性得分与 top-3 设施贡献份额。

- **`network.gravity_access`** 引力可达性（`native`·成熟度 已验证，契约: `gravity_accessibility_analysis`，出处: `hansen1959`, `zipf1946`）
  - 假设：A_i=Σ_j S_j^α/d_ij^β（Hansen 1959 势能/Zipf 1946 引力），S_j=设施容量（α∈[0,3]）；d_ij=路网 OD 成本（活动阻抗，默认行程时间秒）；β∈[0.5,4] —— β 对成本单位敏感，跨阻抗不可比；零成本对（需求与设施捕捉到同点）以 ε=1e-6 下限截断（不丢弃最可达对，除零防护）
  - 局限：不可达对跳过并计数披露（reachable/unreachable_pair_count）；无 cutoff 且全对不可达抛 DisconnectedNetwork；top-3 设施贡献份额为展示截断（仅前 3 项，非全部供给分解）；score 无量纲：只可用于相对排序与截断对比，不做绝对福利解释

## `grid_binning` — 格网聚合

点聚合入 H3 六边形/渔网格网。

- **`spatial.grid.h3`** H3 六边形聚合（`native`·成熟度 —）
  - 回退：`spatial.grid.fishnet`→approximation
- **`spatial.grid.fishnet`** 渔网格网聚合（`native`·成熟度 —）
  - 回退：`spatial.grid.h3`→approximation

## `gwr` — 地理加权回归 (GWR)

空间非平稳性探测：逐观测局地 WLS（bisquare 自适应核）。

- **`spatial.gwr`** 地理加权回归（GWR）（`native`·成熟度 已验证，契约: `gwr_analysis`，出处: `brunsdon1996`, `fotheringham2002`）
  - 假设：自适应 bisquare 核，带宽=最近邻数 k（默认 30，钳制 [5,n/2]）；bandwidth_selection=cv 时在有界网格上留一 CV（确定性穷举）；AIC/AICc 用帽矩阵迹 q=tr(S)+1 的高斯形式（常用近似）
  - 局限：局部共线性会让局部系数失真（全局 VIF 不代表局部）；AICc 没有唯一公认公式——比较带宽/模型时保持同一实现；CV 带宽选择在有界网格上，非连续优化
- **`spatial.mgwr`** 多尺度地理加权回归（MGWR）（`planned`·成熟度 —，出处: `fotheringham2002`）
  - 假设：每个解释变量独立带宽的反向拟合（backfitting）
  - 局限：MGWR 反向拟合未实现——planned 条目，运行时会诚实拒绝

## `hotspot` — 热点显著性分析

Getis-Ord Gi* 等空间聚类显著性检验。

- **`spatial.hotspot.local`** 局部热点显著性（Getis-Ord Gi*）（`native`·成熟度 —）

## `interpolation_model_selection` — 插值模型选择

以 LOOCV/CV 证据比较可行插值方法并确定性推荐（RMSE 排名）。

- **`interpolation.model_compare`** 插值模型比较（`native`·成熟度 已验证，契约: `interpolation_model_compare`，出处: `shepard1968`, `matheron1963`）
  - 假设：以各方法库级 LOOCV/CV 证据（RMSE）排名——方法选择基于证据而非惯例；固定方法序 + 预算序贯走查：完全确定性（同输入必同表）；样本下限：idw≥2/tin≥4/trend≥6/rbf≥3/kriging≥20（各方法 CV 下限）
  - 局限：CV 证据是样本内泛化估计——极端外推场景不外推结论；cv_budget 预算走查按固定方法序跳过后续方法（跳过原因逐行披露）；比较运行于样本（无表面输出）；最终表面仍需调用对应插值工具
  - 回退：`interpolation.idw`→approximation

## `join_count_statistics` — Join Count 统计

二值场的邻接同异类连接计数检验（Cliff-Ord free sampling）。

- **`stats.join_count`** Join Count（二值空间关联）（`native`·成熟度 已验证，契约: `join_count_analysis`，出处: `cliff_ord1973`, `moran1950`）
  - 假设：字段必须 ⊆ {0,1}，含 0 与 1 两个类（违者 UnsupportedMethod）；二值对称权重；n_BB/n_BW/n_WW 按无序连接计数；期望/方差用 free sampling（Cliff-Ord 1973）解析式，n≥4
  - 局限：free sampling 忽略权重结构细节（只含连接数 J）；knn 权重是邻接的近似；queen/rook 需要面要素；小 n 下解析 z 的正态近似偏乐观

## `kde_density` — 核密度估计

KDE 连续密度面/等值线（定量密度表达）。

- **`spatial.kde.contours`** 核密度等值线（`native`·成熟度 —）
  - 回退：`spatial.kde.surface`→equivalent
- **`spatial.kde.surface`** 核密度全格网表面（`native`·成熟度 —）
  - 回退：`spatial.kde.contours`→equivalent

## `local_gearys_c` — 局部 Geary's C

局部相似性/相异性检测（Local Geary's C_i，Anselin 1995；与 LISA 的方向配对互补）。

- **`stats.local_geary`** 局部 Geary's C（相似性/相异性）（`native`·成熟度 已验证，契约: `local_geary_analysis`，出处: `anselin1995`, `geary1954`, `holm1979`, `benjamini_hochberg1995`）
  - 假设：C_i=Σ_j w_ij(z_i-z_j)²，z 为总体方差标准化（esda.Geary_Local 同式）；行标准化权重；置换检验固定种子 42、双侧 (count+1)/(perms+1)；多重校正默认 BH-FDR（可 bonferroni/holm/none）
  - 局限：Local Geary 只判相似/相异，高-低方向配对用 LISA（h3_lisa）；±1 二值场等离散取值下置换分布退化，p 分辨率受格子限制；逐格校正后 α=0.05 判定在随机数据下仍有 ~0.05q 假显著期望

## `local_morans_i` — 局部莫兰/LISA

局部热点/冷点聚类。

- **`stats.h3_lisa`** H3 LISA 局部自相关（`native`·成熟度 已验证，出处: `anselin1995`）
  - 假设：esda.Moran_Local（Queen 邻接、行标准化、seed=42）；孤岛格网给中性结果（p=1、q=0），保持行对齐（#927）；输入为带数值字段的 H3 网格（如 h3_binning 产物）
  - 局限：逐格 p_sim<0.05 在随机数据下期望产出 ~0.05n 假显著（结果内披露期望数）；H3 分辨率改变邻接结构，跨分辨率结果不可比

## `location_allocation` — 区位配置

设施选址-分配优化（tier-3 门控）。

- **`network.location_allocation`** 区位配置（`native`·成熟度 已验证，出处: `teitz_bart1968`, `hakimi1964`）
  - 假设：p_median 目标 = 最小化 Σ w_i·min_{j∈S} C_ij；max_coverage = 最大化 cutoff 内覆盖需求权重；p_center（Foundation V2 A4，Hakimi 1964 max-min）= 最小化可达需求的最大服务成本（打平按总加权成本次级判据）；代价矩阵 = 路网 OD 行程时间（不可达 = inf，参与目标时按 1e9 惩罚）
  - 局限：启发式 >20k 组合；exact ≤20k —— C(m,p) 枚举在预算内给出精确最优，超出切 Teitz-Bart 顶点替换 / 贪婪覆盖 / p-center 贪婪+顶点替换（≤10 轮）；不可达需求点列入 summary.unassigned_ids（不参与选址目标）；Teitz-Bart / p-center 启发式收敛依赖初始化（前 p 个候选），无多起点重启（summary.solver 披露 exact|heuristic）

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

## `nearest_neighbor_functions` — 最近邻距离函数

G/F/J 距离函数（Diggle 1983 / van Lieshout–Baddeley 1996）——最近邻与空空间分布的 CDF 对比 CSR，配固定种子模拟包络。

- **`point_pattern.g_f_j`** G/F/J 距离函数（`native`·成熟度 已验证，契约: `g_f_j_analysis`，出处: `diggle1983`, `van_lieshout_baddeley1996`, `ripley1976`）
  - 假设：G(r)=最近邻距离 CDF；F(r)=空空间函数（确定性低差异查询格）；J(r)=(1−G)/(1−F)，CSR 下 J≡1（van Lieshout–Baddeley 1996）；F 查询格：default_rng(42) 均匀点，n_f=min(4n, 2000)，观测/模拟共用
  - 局限：无边缘校正（矩形窗 Reduced-Sample 未实现）——边界点低估 G/F；J 在 F(r)→1 时分母退化记 NaN（j_undefined_from 披露）；p 值来自秩检验（+1 校正），分辨率 1/(envelopes+1)，上限 499

## `network_centrality` — 网络中心性

路网节点中心性（度/接近/介数/边介数）：精确 Brandes 与固定种子采样两种实现变体，规模护栏先行。

- **`network.centrality`** 网络中心性（`native`·成熟度 已验证，契约: `network_centrality_analysis`，出处: `brandes2001`）
  - 假设：度数 = 入度+出度（DiGraph 语义：单行路段计数不对称，如实呈现）；接近/介数以边权为距离最小化（travel_time_s 秒 / length_m 米），非跳数；介数 Brandes 精确 n≤2000；n>2000 切 k=500 固定种子 42 采样（betweenness_mode 披露）
  - 局限：节点上限 20000（计算前 ResourceScaleMismatch 显式拒绝，不 OOM）；edge_betweenness 仅边数≤1500 精确；超出诚实拒绝（不做假采样）；逐节点输出上限 5000 行（按主指标降序裁剪，output_rows_trimmed 披露）

## `od_flow_mapping` — OD 流向图

把 OD 对（坐标+权重）构建为有界流向线要素层。

- **`flow.od_arc_build`** OD 流向构建（`native`·成熟度 —）

## `od_matrix` — OD 成本矩阵

多起点×终点网络成本矩阵。

- **`network.od_matrix`** OD 成本矩阵（`native`·成熟度 已验证，契约: `network_od_matrix`，出处: `dijkstra1959`）
  - 假设：每个唯一起点一趟累积式 Dijkstra（#449），距离/时间沿同一最短路树累积（GIS-19）；cutoff_s 以活动阻抗为单位（秒/米）；超出预算的对以 reachable=False + inf 返回，绝不静默缺行；有向图语义：单行路网下 OD(A→B) ≠ OD(B→A)
  - 局限：OD 树代价不含转向惩罚（树无路径上下文，#455 跨工具语义）；起点/终点捕捉在 500 m 容差内静默吸附最近边；捕捉距离在结果 snap_evidence 中逐端点披露

## `pair_correlation_function` — 成对相关函数

成对相关函数 g(r)=K′(r)/(2πr)（Illian 2008）——随半径的聚集/规则尺度谱，配固定种子 CSR 包络。

- **`point_pattern.pcf`** 成对相关函数 g(r)（`native`·成熟度 已验证，契约: `pcf_analysis`，出处: `illian2008`, `ripley1976`）
  - 假设：g(r)=K′(r)/(2πr)：由各向同性校正 K 的离散导数 + Epanechnikov 平滑；bandwidth（米）缺省 0=一个 r 步宽（自动值在输出披露）；CSR 参考 g≡1；g>1 聚集 / g<1 规则
  - 局限：g 由 K 的离散导数间接估计，r 网格粒度限制分辨率；Epanechnikov 平滑带宽敏感：小带宽噪声大、大带宽抹平峰值；O(n²) 成对统计，上限 2 万点（超出诚实拒绝）

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
- **`point_pattern.nni`** 最近邻指数（NNI）（`native`·成熟度 已验证，出处: `clark_evans1954`）
  - 假设：R=观测最近邻均值/CSR 期望（0.5·√(A/N)，A 取 bbox）；R<0.7 聚集 / >1.3 分散的阈值为经验分档（非检验）；z=(R̄−E)/SE，SE=√((4−π)/(4πNρ))，ρ=N/A（Clark-Evans 1954）
  - 局限：正态近似 p 在小样本/边缘效应下有偏（无蒙特卡洛包络）；bbox 面积作 CSR 期望，窗形偏离矩形时期望偏；零面积 bbox（全重合点）下 z 检验不可用（nni_test_note 披露）
- **`point_pattern.ripley_k_env`** Ripley's K + CSR 模拟包络（`native`·成熟度 已验证，契约: `ripley_k_envelope_analysis`，出处: `ripley1976`）
  - 假设：与 point_pattern.ripley_k 同一估计器（isotropic 边缘校正）；包络：envelopes 次同 n、同窗同质 Poisson 模拟（固定种子 42）；逐半径秩双侧 p 值（+1 校正）；观测 K 与包络同估计器可比
  - 局限：p 值分辨率 1/(envelopes+1)，上限 499；模拟重跑 K 估计器：envelopes 大 × n 大时计算量线性放大；非矩形研究域的边缘校正按外接矩形近似

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

## `raster_dimensionality_reduction` — 波段降维（PCA）

多波段栅格 SVD 主成分分析（协方差/相关 PCA、explained variance、载荷与前 k 分量栅格）。

- **`remote.pca`** 波段栈 PCA（SVD 降维）（`native`·成熟度 已验证，契约: `raster_pca_analysis`）
  - 假设：标准 SVD/PCA 无单一经典出处声明——method_references 诚实留空；公共有效掩膜：任一波段无效 → 整行剔除（非 pairwise-complete）；standardize=False 协方差 PCA / True 相关矩阵 PCA（方差 ddof=1）
  - 局限：无流式实现：n_bands·H·W ≤ 16M 像元，超限先拒绝（不假装可扩展）；载荷符号不唯一（SVD 符号约定）——跨运行比较需固定实现版本

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

## `regression_kriging` — 回归克里金

OLS 趋势（协变量）+ 残差克里金的混合插值（Odeh 1995）。

- **`interpolation.regression_kriging`** 回归克里金（`native`·成熟度 已验证，契约: `regression_kriging_analysis`，出处: `odeh1995`, `matheron1963`）
  - 假设：RK = OLS 趋势（z ~ 协变量）+ 残差普通克里金（auto 变异函数）；目标处协变量值由样本协变量经 IDW（k=5, power=2）近似——approximate 语义；rk_variance 仅含残差克里金方差；趋势系数不确定性未传播（如实披露）
  - 局限：协变量场在目标处不可知——IDW 近似误差进入趋势项（approximate=True）；常量协变量（零方差）结构化拒绝（DegenerateData）；至少 2 个协变量；EPSG:3857 工作 CRS 的 Web Mercator 尺度畸变（与克里金同）
  - 回退：`interpolation.kriging`→approximation

## `route_optimization` — 路线优化

多站点访问顺序优化（VRP，tier-3 门控）。

- **`network.route_optimization`** 路线优化（`native`·成熟度 已验证）
  - 假设：最近邻初始巡游 + 2-opt 局部搜索改进（有向代价矩阵，方向翻转计价 #540）；leg 代价 = 活动阻抗下的路网最短路（OD 树重建，无逐 leg 重复寻路）
  - 局限：NN+2-opt 启发式非精确 TSP：解无最优性保证（迭代上限 100）；不可达 leg 计 1e9 代价（巡游仍连贯，总代价如实累加 inf leg）
- **`network.optimize_route`** 路线优化（VRP）（`native`·成熟度 已验证）
  - 假设：最近邻初始巡游 + 2-opt 局部搜索改进（有向代价矩阵，方向翻转计价 #540）；leg 代价 = 活动阻抗下的路网最短路（OD 树重建）
  - 局限：NN+2-opt 启发式非精确 TSP：解无最优性保证（迭代上限 100）；stops 上限 200（工具层显式拒绝超限，2-opt 超线性）

## `sar_analysis` — SAR 时序/极化分析

SAR 时序栈统计（含 CV/鲁棒分位数）、时序合成、VV/VH 极化比与双时相对数比值（滤波/定标为独立能力：sar_speckle_filtering / sar_radiometric_calibration）。

- **`sar.temporal_composite`** SAR 时序栈合成（mean/median/percentile）（`native`·成熟度 已验证，契约: `sar_temporal_composite_analysis`，出处: `oliver_quegan1998`）
  - 假设：时间维聚合为描述性合成（median 为斑点拖尾下的鲁棒惯用）；nodata/NaN 逐切片剔除；全切片无效像元 → NaN（披露）
  - 局限：无滤波/定标隐式前置（独立原生算法见 sar.speckle_filter 等）；栈深 ≤24、H·W ≤4096×4096，超限 ResourceScaleMismatch 先拒绝
- **`sar.temporal_stats`** SAR 时序栈统计（`native`·成熟度 已验证，契约: `sar_temporal_stats_analysis`）
  - 假设：输入假定已几何校正并对齐；std 为总体标准差（ddof=0）；nodata/NaN 逐切片剔除，剩余有效切片上统计（部分有效像元披露）；CV=std/mean（可选）：|mean|≤1e-12 → NaN；dB 域 CV 无物理量纲（披露）
  - 局限：本工具无滤波/定标隐式前置——独立原生算法见 sar.speckle_filter/sar.radiometric_calibration；栈深 ≤24、H·W ≤4096×4096，超限 ResourceScaleMismatch 先拒绝
- **`sar.vh_ratio`** SAR VV/VH 极化比（`native`·成熟度 已验证）
  - 假设：VV/VH：线性域为比值、dB 域为 dB 差（VV−VH）；VH=0 → NaN；同景双极化（如 Sentinel-1 VV+VH）
  - 局限：无辐射定标假定下仅作结构对比代理，非物理量
- **`sar.log_ratio_change`** SAR 双时相对数比值变化（`native`·成熟度 已验证）
  - 假设：log(a)−log(b)：对数域对称（增强=衰减镜像），SAR 双期惯用量；经 detect_ratio_change 工具 method=log_ratio 参数执行
  - 局限：比值不区分变化原因；输入须为正（线性强度或 dB）

## `sar_radiometric_calibration` — SAR 辐射定标

DN → β⁰/σ⁰/γ⁰ 常数辐射定标（定标常数显式必需；逐像元 LUT 与热噪声去除未实现——披露）。

- **`sar.radiometric_calibration`** SAR 辐射定标（β⁰/σ⁰/γ⁰ 常数定标）（`native`·成熟度 已验证，契约: `sar_calibration_analysis`，出处: `oliver_quegan1998`）
  - 假设：标准定标关系：β⁰=I/K、σ⁰=β⁰·sin(θᵢ)、γ⁰=β⁰·tan(θᵢ)，I=DN²（振幅域）；calibration_constant（K，如 Sentinel-1 A²/AUT）显式必需——缺失拒绝；入射角：标量或逐像元平面（与网格同形），(0,90) 开区间（度）
  - 局限：逐像元定标 LUT 未实现（仅常数定标）——LUT 场景精度受限；热噪声去除未实现（Sentinel-1 GRD 噪声底未扣，弱信号偏乐观）；不修正地形起伏（无地形辐射校正/局部入射角模型）

## `sar_speckle_filtering` — SAR 斑点滤波

SAR 相干斑点噪声抑制（Lee 1980 / Refined-Lee 边缘方向 MMSE / Frost 1982；ENL 显式优先、缺省矩估计披露；refined_lee 为 7 子窗近似实现）。

- **`sar.speckle_filter`** SAR 斑点噪声滤波（Lee/Refined-Lee/Frost）（`native`·成熟度 已验证，契约: `sar_speckle_filter_analysis`，出处: `lee1980`, `lee1981`, `lopes1990`, `frost1982`）
  - 假设：斑点为乘性噪声（x=R·n）；输入须线性强度（非负，dB 被拒绝）；ENL 显式参数优先；缺省整图矩估计 ENL=mean²/var（均匀假设，披露）；窗口 ∈ {3,5,7}；窗口统计 nodata 感知（全无效窗口 → NaN）
  - 局限：refined_lee 子窗选择为 MSE 代理（方差+中心偏差²）——非 Lopes 1990 完整 MAP 变体；斑点抑制同时平滑真实纹理；不恢复被斑点淹没的像元信息；边界：lee/refined_lee 窗口统计 reflect 补齐（frost 有效集归一）

## `sar_texture` — GLCM 纹理特征

窗口化 GLCM 纹理属性（Haralick 1973：contrast/homogeneity/entropy 等 9 项；纯 numpy 手工实现，量化 2-98 分位、P+Pᵀ 对称、多方向均值）。

- **`sar.glcm_texture`** GLCM 纹理特征（Haralick 窗口化）（`native`·成熟度 已验证，契约: `sar_glcm_texture_analysis`，出处: `haralick1973`）
  - 假设：量化：有效像元 2-98 分位线性拉伸到 levels 档（越界钳端点）；对称约定 P+Pᵀ（±d 同线）；d=1；多方向=逐方向属性 NaN 感知均值；entropy 为自然对数；纯 numpy 手工实现（scikit-image 非声明依赖）
  - 局限：零方差/无有效对窗口 → NaN（correlation 不伪造）；操作规模 H·W·window²·n_dir ≤ 64M 估算上界，超限先拒绝

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

## `space_time_interaction` — 时空交互检验

Knox 时空交互检验（1964）——事件在空间与时间上是否同时邻近（如传染病聚集），时间置换 p 值。

- **`spatiotemporal.knox`** Knox 时空交互检验（`native`·成熟度 已验证，契约: `knox_analysis`，出处: `knox1964`）
  - 假设：观测=同时落在 critical_distance（米）与 critical_time（秒）内的点对数；独立零假设期望 E=2·S·T/(n(n−1))；时间置换（固定种子 42）给单侧 p；critical_distance=0 → 自动取中位最近邻距离（输出披露）
  - 局限：阈值（距离/时间）敏感且结果随阈值变化——建议多阈值对照；时间置换保边际分布，不校正时空趋势（Mantel 类检验更合适）；空间邻近对经 query_pairs 稀疏化，预算超限诚实拒绝

## `spatial_interaction` — 空间相互作用

Huff 概率模型 P_ij：需求点选择各设施的概率、市场份额、专属（captive）份额与份额熵。

- **`network.huff_interaction`** Huff 空间相互作用（`native`·成熟度 已验证，契约: `huff_interaction_analysis`，出处: `huff1964`）
  - 假设：P_ij=A_j·d_ij^−β/Σ_k A_k·d_ik^−β（Huff 1964），A_j=设施容量（吸引力，缺省 1.0）；d_ij=路网 OD 成本（活动阻抗，默认秒）；候选集 = cutoff（活动阻抗单位）内可达设施；熵为自然对数 Shannon 熵（按全部候选份额）；captive=候选集恰为单设施 {j} 的需求权重占比
  - 局限：零成本对以 ε=1e-6 截断；容量 0 的设施吸引力为 0（合法——份额为 0，非错误）；候选集为空的需求点列入 unassigned_demand_ids（不虚构份额，不出现在分母）；概率即期望客流占比的假设模型：不做随机效用离散选择估计/参数标定

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

## `spatial_regression` — 空间回归

OLS+空间诊断 / SLX / SAR-ML / SEM-ML（LM 决策树支撑）。

- **`spatial.ols_regression`** OLS + 空间诊断（`native`·成熟度 已验证，契约: `ols_regression_analysis`，出处: `anselin1988`, `jarque_bera1980`, `breusch_pagan1979`, `moran1950`）
  - 假设：y~X（含截距）；lstsq 求解，se/t/p 由 (X'X)⁻¹σ² 给出；残差 Moran's I 固定种子 42 置换（双侧 +1）；LM-lag/LM-error/稳健版与 spreg LMtests 逐式一致（Anselin 1988）
  - 局限：残差 Moran 显著时只给 SAR/SEM 建议文本，不替用户自动换模型；n < 2p+2 拒绝（InsufficientSamples）；VIF 在仅一个解释变量时不可得（诚实留空）
- **`spatial.sar_ml`** 空间滞后 ML（SAR）（`native`·成熟度 已验证，契约: `sar_ml_analysis`，出处: `ord1975`, `anselin1988`）
  - 假设：y=ρWy+Xβ+ε；log|I-ρW|=Σ ln(1-ρκᵢ)（Ord 1975 特征值法）；ρ 在平稳域 (1/κ_min,1/κ_max) 内有界 Brent 最大化（确定性）；LR 检验 vs OLS（df=1）；伪 R²=1-SSE_SAR/SSE_OLS
  - 局限：n>4000 拒绝（特征值 O(n³)，ResourceScaleMismatch 先于分配）；仅支持相似对称权重（knn/queen/rook/distance_band 均满足）；运行时不静默回退 OLS——fallback 声明只供规划层参考
  - 回退：`spatial.ols_regression`→approximation
- **`spatial.sem_ml`** 空间误差 ML（SEM）（`native`·成熟度 已验证，契约: `sem_ml_analysis`，出处: `ord1975`, `anselin1988`）
  - 假设：y=Xβ+u，u=λWu+ε；Cy=CXβ+ε 的 GLS 剖面似然（C=I-λW）；与 SAR 同一特征值机器；λ 有界 Brent 最大化（确定性）；LR 检验 vs OLS（df=1，Burridge 1980 LM-error 的 ML 对应）
  - 局限：n>4000 拒绝（特征值 O(n³)）；运行时不静默回退 OLS——fallback 声明只供规划层参考
  - 回退：`spatial.ols_regression`→approximation
- **`spatial.slx`** SLX（空间滞后 X 的 OLS）（`native`·成熟度 已验证，契约: `slx_analysis`，出处: `anselin1988`, `cliff_ord1973`）
  - 假设：y~[X, WX]；WX 为行标准化权重的空间滞后解释变量；系数表含 WX 滞后项（邻居溢出的直接估计）；孤岛观测的 WX 行为 0（披露于 weights 元数据）
  - 局限：直接/间接效应分解未做（需 SAR/SDM 类模型的偏导推导）；参数量翻倍，n<2p+2 时拒绝

## `spatiotemporal_clustering` — 时空聚类

ST-DBSCAN 等时空聚类（与 LISA 局部自相关是不同检验）。

- **`temporal.hotspot`** 时空热点（`native`·成熟度 —）
- **`stats.st_dbscan`** 时空 DBSCAN 聚类（`native`·成熟度 —）

## `spectral_index` — 类型化光谱指数

按语义角色（red/nir/swir1/...）显式命名的 12 公式族光谱指数（含出处与值域诚实报告）。

- **`remote.spectral_index`** 类型化光谱指数（11 公式族）（`native`·成熟度 已验证，契约: `spectral_index_analysis`，出处: `rouse1974`, `huete1988`, `gao1996`, `xu2006`, `zha_woodcock2003`, `key_benson2006`, `mcfeeters1996`）
  - 假设：波段按语义角色显式命名（band_map），绝不按波段位置猜测；线性定标先于公式（DN/10000→反射率）；零分母→NaN；超理论值域只报告不钳制（out_of_range_fraction）
  - 局限：公式出处逐指数声明（gndvi/msavi/ndmi 无词表出处，诚实留空）；EVI/EVI2 常数项只在反射率单位下成立（#382）

## `tasseled_cap_transformation` — Tasseled Cap 冠层变换

传感器系数注册表驱动的亮度/绿度/湿度三轴变换（landsat5_tm=crist_cicone1984、landsat8_oli=baig2014、sentinel2=shi_xu2019；六语义角色显式映射）。

- **`remote.tasseled_cap`** Tasseled Cap 冠层变换（传感器系数注册表）（`native`·成熟度 已验证，契约: `tasseled_cap_analysis`，出处: `crist_cicone1984`, `baig2014`, `shi_xu2019`）
  - 假设：系数行按传感器显式注册（landsat5_tm/landsat8_oli/sentinel2）；波段按六语义角色显式映射（blue/green/red/nir/swir1/swir2）；reflectance_domain 仅披露（baig2014/shi_xu2019 于 at-satellite 推导）
  - 局限：线性变换不改信息总量（3 轴是 6 波段旋转投影，非独立观测）；未注册传感器显式拒绝（不默认套用他传感器系数）

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

## `terrain_geomorphometry` — 地貌形态分类

地形开放度（Yokoyama 2002）、geomorphons 地貌分类（Jasiewicz & Stepinski 2013）、Weiss 双尺度 TPI 地类分级与多方位山体阴影。

- **`terrain.openness`** 地形开放度（`native`·成熟度 已验证，契约: `openness_analysis`，出处: `yokoyama2002`）
  - 假设：正开放度 = mean_φ max_d arctan((z₀−z(d))/d)；负开放度同式取反向差（度）；16 方位（4-64 可调）× 半径 1..R 像元；偏移圆整后的实际米制距离；平地 ≡ 0；山脊高正开放度、谷地高负开放度幅值
  - 局限：方位离散 ≤ 360/azimuth_count（默认 22.5°）角分辨率；无有效采样的方位从均值剔除（栅格角隅诚实退化）；半径 ≤ 100 像元护栏（射线行走内存/时间包络）
- **`terrain.geomorphons`** Geomorphons 地貌分类（`native`·成熟度 已验证，契约: `geomorphon_analysis`，出处: `jasiewicz_stepinski2013`）
  - 假设：8 方位视线三元码（zenith/nadir 角 vs flatten 容差）→ 10 类决策表；决策表（优先级级联）：全-1 summit；全+1 depression；≥6 环 ridge/valley；双 3-5 环 slope；单 3-5 环 shoulder/hollow；1-2 环 spur/footslope；否则 flat；far>0 跳过近场采样（skip 半径）；无采样腿按 0（平）计并披露
  - 局限：相对高程形态学：无绝对坡度语义（缓坡大尺度可判 flat）；lookup ≤ 128 像元护栏；flatten=0 时 DEM 噪声直通分类；角隅像元方位被网格截断（无采样腿按平计）
- **`terrain.landform`** 双尺度 TPI 地类分级（`native`·成熟度 已验证，契约: `landform_analysis`，出处: `weiss2001`）
  - 假设：Weiss 2001 双尺度标准化 TPI（TPI/SD）+ 高程百分位 10 类决策表；中性带 |TPI/SD|<1；平地带按 elevation_tolerance 截 percentile 分档；TPI 窗口含中心像元（与 terrain.tpi 同口径）；边缘收缩
  - 局限：窗口与容差需按景观尺度率定（缺省 3/25 格、0.1 为海报惯例起点）；常量面（TPI SD=0）→ DegenerateData（分类阈值无定义）
- **`terrain.hillshade_multi`** 多方位山体阴影（`native`·成熟度 已验证，契约: `hillshade_multiazimuth`，出处: `horn1981`）
  - 假设：单方位公式与 band_math.compute_hillshade 逐位一致（#379 罗盘语义）；combine=mean 多方位均值（去阴影）/ min 逐像元最小（制图）；NaN 像元传播为 NaN（与渲染掩膜一致）
  - 局限：朗伯面近似：无次级散射/大气效应；3×3 Horn 梯度 edge 复制延拓（单侧差分）

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

## `terrain_hydrology_advanced` — 高级地形水文

Priority-Flood 填洼（Barnes 2014，epsilon 单调变体）、D∞ 多向流（Tarboton 1997 比例分流）、流程长度、河网提取与 Strahler 分级、流域形态量测（Strahler 1957）。

- **`terrain.sink_fill`** Priority-Flood 填洼（`native`·成熟度 已验证，契约: `sink_fill`，出处: `barnes2014`）
  - 假设：Priority-Flood（Barnes 2014）heapq 漫水；种子 = 网格边界 + nodata 邻接有效像元；nodata/网格外视作排水出口；epsilon>0 时逐像元抬升 → 表面严格单调可排；meta 报告 filled_volume（z_units·m²）/filled_cell_count/max_fill_depth
  - 局限：epsilon=0 时填后平地仍为汇（与 d8 不发明路由语义衔接）；纯 Python 堆循环，>10M 像元耗时显著（护栏 50M 像元先拒绝）；无嵌套洼地深度分层报告（单层溢流面）
- **`terrain.dinf_flow`** D∞ 多向流（`native`·成熟度 已验证，契约: `dinf_analysis`，出处: `tarboton1997`）
  - 假设：8 三角面平面梯度最陡下降（Tarboton 1997）；角度弧度 ∈ [0,2π)，x=东 y=北；汇流按面内角度比例分流到两下游邻域；拓扑序（高程降序）累积；平地/洼地 → 角度 -1 哨兵；nodata → NaN；函数内不填洼
  - 局限：D∞ 不消解格网平行流向偏差的极端情形（面离散 45°）；推荐组合 fill_depressions(epsilon>0) 先行获得单调可排面；缺角邻域的面跳过（边缘只用可得邻域）
- **`terrain.flow_length`** 流程长度（`native`·成熟度 已验证，契约: `flow_length_analysis`，出处: `tarboton1997`, `strahler1957`）
  - 假设：downstream = 沿 D8 接收者到出口的米制步长和（汇/出口 = 0）；upstream = 距最远山脊源的最大路径长（MAX 口径，文档化）；步长 = hypot(Δcol·cx, Δrow·cy)；地理栅格由调用方传 cos(lat) 修正 cx
  - 局限：继承 D8 格网流向偏差（路径沿 8 邻域折线）；平地不路由（d8 code 0）→ 平地内长度为 0
- **`terrain.streams`** 河网提取（`native`·成熟度 已验证，契约: `stream_network`，出处: `strahler1957`）
  - 假设：河网像元 = 汇流累积 ≥ threshold（上游贡献像元数口径）；阈值由调用方按流域尺度率定（无普适默认）
  - 局限：阈值敏感：过低生成伪河网、过高断头（无自动率定）；继承 D8 单向流的河网走向偏差
- **`terrain.strahler`** Strahler 河流分级（`native`·成熟度 已验证，契约: `stream_network`，出处: `strahler1957`）
  - 假设：Strahler 1957：源头 = 1 级；最高上游级唯一 → 同级，并列 → +1；拓扑序 = 高程降序（接收者严格更低；同高程 (row,col) 兜底）；meta 报告 order_distribution 与 max_order
  - 局限：河网输入依赖 accumulation 阈值（见 terrain.streams 局限）；格网平行汇流会高估并列（+1 升级）频率
- **`terrain.morphometry`** 流域形态量测（`native`·成熟度 已验证，契约: `morphometry_analysis`，出处: `strahler1957`）
  - 假设：面积/周长来自逆 D8 上流域掩膜；周长 = 边界边缘长度和（网格外视作流域外）；basin length = 流域内 MAX upstream 流程长度（最长山脊→出口路径）；form factor = A/L²；elongation = 2√(A/π)/L（Strahler 1957）
  - 局限：basin length 的 MAX 口径对狭长流域外的形状敏感（非主轴拟合）；排水密度继承河网阈值敏感性；pour point 不做河道 snap

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

## `terrain_wetness_indices` — 湿润与侵蚀指数

地形湿润指数 TWI（Beven-Kirkby 1979）、水流功率指数 SPI 与 USLE LS 因子（Wischmeier-Smith 1978 / Desmet-Govers 1996）。

- **`terrain.twi`** 地形湿润指数 TWI（`native`·成熟度 已验证，契约: `wetness_index`，出处: `beven_kirkby1979`）
  - 假设：TWI = ln(SCA/tanβ)；SCA = (accum+1)·cell_area/contour_width；等流宽度 = cell_size（y 向）；κ=1 flat 口径（单流向近似，meta 披露）；tanβ 下限 1e-6：平地处 TWI 为截断上界（非物理解）
  - 局限：D8/D∞ 单向累积低估发散坡的 SCA（无多向 κ 分解）；slope 与 accum 网格必须同形对齐（无重采样）
- **`terrain.spi`** 水流功率指数 SPI（`native`·成熟度 已验证，契约: `wetness_index`，出处: `beven_kirkby1979`）
  - 假设：SPI = SCA·tanβ（侵蚀/输沙潜势代理）；SCA 口径同 terrain.twi；tanβ 无下限（平地 → SPI 0）
  - 局限：静态地形代理，无降雨/土壤参数（非过程模型）；SCA 单流向近似偏差同 terrain.twi
- **`terrain.ls_factor`** USLE LS 因子（`native`·成熟度 已验证，契约: `ls_factor_analysis`，出处: `wischmeier_smith1978`, `desmet_govers1996`）
  - 假设：mccool：LS=(λ/22.13)^m·(65.41sin²θ+4.56sinθ+0.065)；m 表 McCool 1987：<1%→0.2、1-3%→0.3、3-5%→0.4、≥5%→0.5；desmet_govers：LS=(m+1)·(SCA/22.13)^m·(sinβ/0.0896)^1.3；SCA 口径同 TWI；λ 建议传 upstream 流程长度（缺省固定 100 m，meta 披露）
  - 局限：标准径流小区经验式的栅格外推（无降雨/植被因子）；n=1.3 固定（Desmet-Govers 1996 实现惯例），不暴露调参

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

## `trend_surface` — 趋势面分析

全局多项式趋势面（阶数 1-3 OLS），R²/残差方差证据，外推逐格标记。

- **`interpolation.trend_surface`** 趋势面分析（`native`·成熟度 已验证，契约: `trend_surface_analysis`，出处: `webster_oliver2007`）
  - 假设：全局多项式 OLS：z ~ u^i·v^j（i+j≤order），坐标缩放至单位盒（条件数稳定，已披露）；OLS 残差方差 σ̂²=SS_res/(n−p) 是有效的模型方差证据（区别于克里金逐点方差）；bbox 外评估照常输出但逐格标记 extrapolated（趋势模型本就全局外推）
  - 局限：全局多项式只能表达大尺度趋势——局地结构交给克里金/TIN/RBF；高阶多项式边缘振荡（Runge 现象）：order≤3 硬限制；坐标零跨度/设计矩阵不满秩结构化拒绝（DegenerateData）
  - 回退：`interpolation.kriging`→approximation

## `triangulation_interpolation` — 三角网插值

Delaunay TIN 三角网插值（linear / clough_tocher），凸包外不外推。

- **`interpolation.tin`** TIN 三角网插值（`native`·成熟度 已验证，契约: `tin_interpolation`，出处: `watson1981`, `clough_tocher1966`）
  - 假设：Delaunay 三角剖分上的分段插值：linear=C⁰ 重心插值，clough_tocher=C¹ 三次；凸包外诚实空缺（fill_value=NaN）：TIN 不外推，格网外的缺失进 metadata；米制坐标下剖分：地理输入经 estimate_utm_crs 自动投影（与 IDW 同 CRS 政策）
  - 局限：样本共线/退化构型结构化拒绝（DegenerateData，附修正提示）；>20 万样本拒绝（Qhull 内存有界但超限先抽稀）；凸包外格网无值——需要全域覆盖时改用 IDW/趋势面（会外推）
  - 回退：`interpolation.idw`→approximation

## `voronoi_tessellation` — Voronoi 剖分

点的 Voronoi/Thiessen 有限区域剖分（镜像外推 + 范围裁剪）。

- **`geometry.voronoi`** Voronoi（Thiessen）剖分（`native`·成熟度 已验证）
  - 假设：scipy Voronoi + 4 轴镜像点外推使边界点获得有限区域；输出按数据范围 +50% 边距裁剪（可用 clip_bounds 显式指定）；每个点格的质心作为剖分种子（非点输入取质心）
  - 局限：边界镜像外推：裁剪框外的区域形状依赖镜像几何，非真实边界；重复点行为：Qhull 退化时诚实报错（QH6154）；一般重复点各得一份相同区域（不去重）；无单元格的退化区域被静默跳过（count < 输入点数）

## `weights_sensitivity` — 权重敏感性

同一统计量在多个空间权重方案下的结论稳定性包络。

- **`stats.weights_sensitivity`** 权重方案敏感性（Moran's I）（`native`·成熟度 已验证，契约: `weights_sensitivity_analysis`，出处: `moran1950`, `anselin1988`）
  - 假设：方案集固定：knn(k)/queen/rook/distance_band(auto 8nn)；queen/rook 对点输入如实跳过并披露（不做静默替换）；逐方案 Moran I + 固定种子 42 置换 p；判读多数一致性=稳定性
  - 局限：四方案是常见代表性集合，不是穷举权重空间；稳定性=判读一致比例，不代表 I 的点估计置信区间

## `zonal_statistics` — 分区统计

面内栅格 min/max/mean/sum 统计。

- **`remote.zonal_stats`** 分区统计（`native`·成熟度 —）
