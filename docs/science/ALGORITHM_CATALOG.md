# 算法目录（自动生成 · ADR-0099 §63）

> **本文件由注册表生成，请勿手工编辑。** 事实源：
> `app/lib/gis/algorithms/`（算法）、`app/lib/gis/capabilities/`（能力）、
> 各域包 `PARAMETER_CONTRACTS`（参数契约）。
> 再生成：`python scripts/gen_science_catalog.py`。

统计：123 能力 · 192 算法 · 112 参数契约。

## `accessibility` — 网络可达性

需求点对设施集合的可达性指标计算（15 分钟生活圈等）。

- **`network.accessibility`** 网络可达性（`native`·成熟度 已验证，契约: `network_accessibility_analysis`，出处: `luo_wang2003`, `luo_qi2009`）
  - 假设：2SFCA: 供给/需求两步浮动捕获 —— 第一步 R_j=容量_j/catchment 内需求权重和，第二步 A_i=Σ(cutoff 内 R_j)（Luo & Wang 2003）；E2SFCA（Foundation V2 A4，Luo & Qi 2009）：cutoff 等分 decay_zones 带，带中点高斯权 w_r=exp(−0.5·(r+0.5)²)；15min_circle 法：需求点在 cutoff 内可达任一设施即计入 served（0/1 覆盖，非 2SFCA）
  - 局限：容量/需求比值代理；2SFCA cutoff 内等权，E2SFCA 按等分带高斯衰减（decay_zones 1-10，缺省 3）——方法即精度权衡；容量缺省 1.0：未提供 capacity 字段时 R_j 退化为供需计数比；供需完全不可达的需求点计入 unserved（显式），score=0 的解释依赖供需总量披露

## `admin_aggregation` — 行政区聚合统计

点落入面聚合（各区数量）。

- **`spatial.aggregate.admin`** 点落入面聚合（行政区统计）（`native`·成熟度 已验证）
  - 假设：空间连接谓词 intersects（边界点计入其贴边多边形，聚合约定）；无点多边形 count=0 且 has_data=False；真 0 与无数据显式区分（#693）；点/面 CRS 不一致时先统一到 UTM 工作帧再连接
  - 局限：count 聚合非密度/率——归一化需显式分母（见 spatial.aggregate.rates）

## `admin_boundary_query` — 行政区边界获取

获取行政区边界面（本地 SHP 优先）。

- **`admin.boundary.local`** 行政区边界获取（本地 SHP）（`native`·成熟度 —）
  - 假设：本地 SHP 边界面获取（不做几何改写）
  - 局限：边界现势性依赖本地数据版本（来源披露）
- **`admin.boundary_lookup`** 行政区边界获取（`native`·成熟度 实验）
  - 假设：行政区边界检索（本地优先，在线兜底）
  - 局限：在线兜底结果可变（deterministic=False 已声明）

## `analytical_density` — 分析密度

定量密度（每平方公里密度等）——拒绝把视觉热力当定量结果。

- **`density.analytical.mixed`** 分析密度（KDE/聚合混合路径）（`native`·成熟度 已验证）
  - 假设：KDE/聚合混合路径按规模切换（切换语义披露）
  - 局限：路径切换以规模阈值为准（诊断进证据块）

## `areal_interpolation` — 面插值（dasymetric）

源统计面总量按控制要素面（可带权重）的面积-权重比例切分重分配；输出 source∩control 碎片面要素集，总量守恒。

- **`interpolation.dasymetric`** 分区密度重分配（`native`·成熟度 已验证，契约: `dasymetric_reallocation`，出处: `wright1936`）
  - 假设：value_field 必须是总量语义（可加：人口/户数/建筑面积）；比率不可重分配；碎片权重 = 控制密度 d_j=w_j/A_j × 碎片面积；控制面密度均质假设（Wright 1936）；权重字段缺失/全零的源退化为纯面积权重插值（逐源计数披露，从不静默）
  - 局限：输出是 source∩control 碎片面 —— 源边界不再出现（渲染按碎片值分级）；控制层未覆盖的源面整面保值（no_ancillary_coverage），不参与密度表达；控制密度均质假设是方法上界：真实人口密度在控制分区内仍有亚片区差异
  - 资源包络：128B/要素
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-06

## `band_math` — 波段/栅格代数

逐像元栅格代数（A/B 表达式、常数运算；A 为基准网格，B 自动对齐）。

- **`raster.algebra`** 栅格计算器（窗口化）（`native`·成熟度 已验证）
  - 假设：窗口化逐像元表达式求值（numexpr）；nodata 传播为 nodata
  - 局限：表达式在声明波段角色上求值，不做隐式重采样/对齐；除零按表达式语义产 inf/NaN（不静默钳制）

## `band_statistics` — 波段统计

波段×波段 Pearson 相关矩阵 + 逐对样本数（公共有效掩膜约定披露；stats_table 产物）。

- **`remote.band_correlation`** 波段×波段相关表（`native`·成熟度 已验证，契约: `band_correlation_analysis`）
  - 假设：Pearson 相关（ddof=1 协方差）；公共有效掩膜（非 pairwise-complete）；逐对样本数恒等于公共有效像元数（约定披露）；standardize 不改变 Pearson r（线性不变性，诚实披露非双结果）
  - 局限：线性相关不捕获非线性关联；零方差波段行列 NaN（不伪造）；公共掩膜 vs 逐对完整计算的差异在低重叠场景显著（披露）

## `bivariate_local_moran` — 双变量局部 Moran

x 与 y 空间滞后的逐位置共位/互斥检测（双变量 LISA；与全局双变量 Moran、单变量 LISA 是不同检验）。

- **`stats.bivariate_local_moran`** 双变量局部 Moran（LISA）（`native`·成熟度 已验证，契约: `bivariate_local_moran_analysis`，出处: `anselin1995`, `wartenberg1985`, `benjamini_hochberg1995`）
  - 假设：esda.Moran_Local_BV 委托（行标准化权重、固定种子 42 条件随机化）；I_i=z(x1)_i·Σ_j w_ij z(x2)_j；标签 HH/LH/LL/HL 取 p_sim<0.05；BH q 值随要素输出；孤岛位置贡献为 0、结果中性
  - 局限：共位相关 ≠ 因果/超前-滞后；方向解读需领域模型支撑；孤岛权重处置与 esda 归一化的对齐仅在无 island 权重时严格成立；p_sim<0.05 的逐点判定在随机数据下期望产出 ~0.05n 假显著

## `bivariate_morans_i` — 双变量 Moran's I

x 与 W·y 的空间共变（Wartenberg 1985；共位相关非因果）。

- **`stats.bivariate_moran`** 双变量 Moran's I（x vs W·y）（`native`·成熟度 已验证，契约: `bivariate_moran_analysis`，出处: `wartenberg1985`, `moran1950`）
  - 假设：I=(n/S0)·Σ x_i(Wy)_i/(‖x-x̄‖·‖y-ȳ‖)，行标准化权重；x=y 时与单变量 Moran 严格一致（属性测试钉住）；置换只打乱 y（固定种子 42，双侧 (count+1)/(perms+1)）
  - 局限：共位相关 ≠ 因果/超前-滞后；方向解读需领域模型支撑；x 与 y 量纲无关（分子分母同除范数），但受离群值影响；与 esda 归一化对齐仅在无 island 权重时成立（S0=n）；含 island 发散 n/S0

## `block_kriging` — 块克里金

块支撑克里金（2×2 离散化，Isaaks & Srivastava 1989）：块均值 + 块方差面。

- **`interpolation.block_kriging`** 块克里金（`native`·成熟度 已验证，契约: `block_kriging_analysis`，出处: `isaaks_srivastava1989`, `matheron1963`，精度: approximate）
  - 假设：2×2 子点离散化近似块均值协方差（Isaaks & Srivastava 1989 惯例，近似已披露）；LHS 保持点支撑样本-样本 γ；块支撑经 RHS γ̄(x,B) 与方差修正 −γ̄(B,B) 进入；块尺寸→0 时收敛到点克里金（rtol 1e-3，conformance 固定）
  - 局限：块尺寸相对变程越大，2×2 离散化近似误差越大（更高密度离散化未实现）；块边界取矩形（H3 单元为六边形——以等面积方形近似，已披露）；块方差 ≤ 点方差仅在平均意义上成立（个别格点可反超）
  - 回退：`interpolation.kriging`→approximation
  - 资源包络：24B/要素，对预算 200000，要素硬上限 500000
  - 取消：chunk_boundary
  - 数值容差：rtol=0.001，atol=1e-09

## `category_breakdown` — 类别构成统计

按类别字段统计构成。

- **`stats.category.breakdown`** 类别构成统计（`native`·成熟度 —）
  - 假设：按类别字段 groupby 计数/占比（描述性）
  - 局限：类别基数过大时 top-k 截断披露（不聚合长尾）

## `change_detection` — 时序要素变化检测

矢量要素的双时相对比变化集（栅格图像变化用 raster_change_detection）。

- **`temporal.change`** 时序变化（`native`·成熟度 已验证）
  - 假设：双期快照对比（描述性集合差：新增/消失/保持）
  - 局限：无匹配容差语义（同键精确匹配）

## `closest_facility` — 最近设施

从需求点到设施集合的 top-K 最近路径。

- **`network.closest_facility`** 最近设施（`native`·成熟度 已验证，出处: `dijkstra1959`）
  - 假设：所有 需求×设施 对的代价来自同一棵逐起点 Dijkstra 最短路树（#489），选 K 近仅在代价上排序；travel_direction 决定方向性：incident_to_facility（需求→设施）或 facility_to_incident；零代价匹配（需求点恰在设施处）是合法匹配（#456）
  - 局限：网络不连通/超出 cutoff 的需求点不产路线，逐一点列入 summary.unmatched_demand_ids（不静默丢弃）；OD 树代价不含转向惩罚（树无路径上下文，#455 跨工具语义）
  - 回退：`network.shortest_path`→approximation

## `cloud_qc_advisory` — 云 QC 咨询掩膜

亮度阈值 + 可选 NDVI 近零的云咨询掩膜（EXPERIMENTAL——非 Fmask/云概率：无热红外、卷云、视差/多时相检验，强披露）。

- **`remote.cloud_qc`** 云 QC 基础咨询（亮度阈值）（`native`·成熟度 实验，契约: `cloud_qc_analysis`）
  - 假设：brightness=(red+nir)/2；阈值=显式绝对值或缺省场景 97.5 百分位；可选 |NDVI| ≤ ndvi_max_abs 条件（云光谱平坦）；零分母不进条件；qc_mask=True=疑似云（advisory）——非云概率产品
  - 局限：EXPERIMENTAL：非 Fmask/cloud-probability——无热红外、卷云、视差/多时相检验（强披露）；亮地物（屋顶/沙地/雪/旱地）误报；云影不检测

## `cokriging` — 协同克里金

协同定位协同克里金（MM1 近似）：主/次变量联合建模；|ρ|<0.2 结构化拒绝。

- **`interpolation.cokriging`** 协同克里金（`native`·成熟度 已验证，契约: `cokriging_analysis`，出处: `journel_huijbregts1978`, `matheron1963`，精度: approximate）
  - 假设：Markov Model 1 近似核化：交叉协方差 C_sy(h)=ρ·C_pp(h)（全交叉协方差未建模）；协同定位近似：次变量仅在目标格点以单一数值进入克里金系统；次变量缺失时取最近次变量值（精确协同定位格数披露）；C_ss(0)=主变量先验方差（标准化假设）
  - 局限：次变量自身变异函数未拟合（MM1 缩放假设）；次变量须与主变量共享同一工作 CRS；非协同定位部分由最近邻补格（近似）；近似语义（approximate=True）：协同克里金理论收益依赖 MM1 假设成立
  - 回退：`interpolation.kriging`→approximation
  - 资源包络：24B/要素，对预算 200000，要素硬上限 500000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-06，atol=1e-09
- **`interpolation.cokriging_lmc`** LMC 全共克里金（`native`·成熟度 已验证，契约: `cokriging_lmc_analysis`，出处: `journel_huijbregts1978`, `goovaerts1997`，精度: exact）
  - 假设：LMC：γ_ij(h)=Σ_u b_ij^u·g_u(h)，共享 2 结构（球状短程/指数长程）；B^u = [[s1u, ρ√(s1u·s2u)],[…, s2u]] —— |ρ|≤1 ⇒ 逐结构半正定（按构造）；全共克里金：主/次变量样本全部进入邻域（非仅目标协同定位）
  - 局限：结构 sill 按 35/65 固定比例分解（非完整 Goulard–Voltz 迭代拟合，近似已披露）；完全复制的次变量（同点位同值）使系统近奇异，方差不可信——次变量须携带独立信息；|ρ|<0.2 类型化拒绝（弱相关不会优于单变量克里金）
  - 回退：`interpolation.kriging`→approximation
  - 资源包络：32B/要素，要素硬上限 500000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-09，atol=0

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

## `cross_pair_correlation` — 双变量成对相关函数 g12

双变量 g12(r)=K12′(r)/(2πr)——两类点空间吸引/相斥随尺度的谱（cross-K 的导数形式），随机标记包络。

- **`point_pattern.cross_pcf`** 双变量成对相关函数 g12(r)（`native`·成熟度 已验证，契约: `cross_pcf_analysis`，出处: `illian2008`, `besag1977`）
  - 假设：g12(r)=K12′(r)/(2πr)：交叉 K12（各向同性校正）的离散导数 + Epanechnikov 平滑（与单变量 pcf 同款后处理）；random-labelling 参考 g12≡1；g12>1 两类吸引/共现，g12<1 相斥；bandwidth（米）缺省 0=一个 r 步宽（自动值在输出披露）
  - 局限：g12 由 K12 的离散导数间接估计，r 网格粒度限制分辨率；每类 ≥5 点（否则诚实拒绝）；O(n²) 成对统计上限 2 万点；p 值来自 sup|g12−1| 秩检验（+1 校正），上限 499

## `dataset_ingest` — 数据集摄入

内联 GeoJSON FeatureCollection 摄入会话：指纹去重、有界画像、质量诊断、产物登记（有界返回，不含数据本体）。

- **`data.ingest.pipeline`** 会话数据摄入管线（`native`·成熟度 —）
  - 假设：内容指纹去重可重复触发（同载荷幂等返回既有 ref）
  - 局限：内联载荷 ≤8MB；更大文件走 POST /upload 通道

## `density_surface` — 视觉密度面

视觉热力（回答『大概哪儿密』，非定量）。

- **`density.visual.heatmap`** 视觉热力（渲染态密度）（`native`·成熟度 已验证）
  - 假设：渲染态密度（栅格化加核）——与解析 KDE 语义分离；（§10 硬规则：不以视觉热力冒充解析 KDE）
  - 局限：带宽/半径为渲染参数（非统计带宽选择器）

## `emerging_hotspot_analysis` — 时空热点演化（EHA）

逐期 Getis-Ord Gi*（BH-FDR）+ 逐箱 Mann-Kendall 的 Emerging Hot Spot 演化分类（new/consecutive/intensifying/persistent/diminishing/sporadic/oscillating/historical + none，热点冷点镜像）。

- **`temporal.emerging_hotspot`** 时空热点演化（EHA）（`native`·成熟度 已验证，出处: `getis_ord1992`, `mann1945`, `kendall1975`, `esri_eha`）
  - 假设：输入为已聚合的空间箱 × 时间期计数量矩阵（space-time cube，H3/格网聚合由调用方完成；某期缺失的箱按 0 计入并披露）；逐期对全箱计算 Getis-Ord Gi* z（距离段二值权重、含自身 w_ii=1），双侧解析 p 经 BH-FDR 校正后判显著（q<alpha）；对每个箱的 Gi* z 值时序跑 Mann-Kendall（tie 校正方差 + 连续性校正）；按 ESRI Emerging Hot Spot Analysis 决策树输出 17 类 + none（互斥完备；类别码 ±1..±8/0）
  - 局限：逐期 Gi* 用纯空间邻域（非时空 lag 邻域）——与 ArcGIS 实现同口径，但对期数少、箱数少的立方显著性偏保守；空间箱 <8 或期数 <8 时正态近似偏保守（仅描述性解读，警告在场）；某期各箱计数全同（零方差）时该期无空间对比，z 置 0 并披露

## `endmember_extraction` — 端元提取

VCA 顶点成分分析端元提取（Nascimento & Dias 2005 的简化确定性变体；纯像元假设；EXPERIMENTAL——结果需人工核验）。

- **`remote.endmember_vca`** 端元提取（VCA）（`native`·成熟度 实验，契约: `endmember_vca_analysis`，出处: `nascimento2005`）
  - 假设：纯像元假设——恢复端元 = 原始像元光谱；随机投影固定 seed；SVD 降维（均值正交补取前 m−1 维）+ 逐顶点随机投影选择；末顶点用已选顶点仿射包法向（带符号极值距离比较）
  - 局限：EXPERIMENTAL：论文完整实现的简化确定性变体（披露），结果需人工核验；守卫：2 ≤ n_endmembers < n_bands（降维到 m−1 维的实现约定）；无纯像元的场景（强混合）恢复端元为近似（凸包顶点，非真实端元）

## `external_route_planning` — 外部路径规划

经外部服务商（高德/百度）API 的点对点路径规划（驾车/步行/骑行/公交），返回距离、耗时与路线坐标；依赖服务商 API Key。

- **`network.route_external_api`** 外部路径规划（高德/百度）（`native`·成熟度 实验）
  - 假设：路线/距离/耗时完全由服务商（高德或百度）路径规划 API 给出，本地不做路网构图；输入为 WGS84 [lng,lat]，由服务商做坐标与路况语义解释
  - 局限：外部依赖：需 AMAP_API_KEY 或 BAIDU_API_KEY；配额/可达性/口径随服务商；结果含 fetched_at 戳：实时路况敏感，逐次调用不可复现（deterministic=False）；与服务商计费口径一致的路线不与本地路网分析（network.shortest_path）互相 fallback

## `federated_dataset_query` — 联邦数据集查询

N 源（2..4）有界左深链式联邦查询：属性/空间连接与聚合逐跳串联，成本排序、最小投影、半连接约减、逐跳预算 fail-fast。

- **`data.federated.chain`** 多源链式联邦查询（`native`·成熟度 —）
  - 假设：左深链计划；半连接右表约减在预算内
  - 局限：逐跳预算 fail-fast；绝不拉全量大表

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
- **`stats.geodetector_ecological`** 地理探测器·生态探测器（`native`·成熟度 已验证，契约: `geodetector_ecological_analysis`，出处: `wang2010`）
  - 假设：SSW_j=Σ_h Σ_{i∈h}(y_i−ȳ_h)²（分层的未解释变异）；t=[SSW₁/(n−m₁)−SSW₂/(n−m₂)]/sqrt(速率方差合成)，Wang 2010 族；双侧 p 用 Student t、df=n−2（保守可复核的 df 选择，meta 披露）
  - 局限：df=n−2 是保守选择：分层自由度的精确合成需 Behrens-Fisher 类近似；SSW 只度量分层解释力，不是因果证据；两分层必须行对齐（任一分层字段为空的行整行丢弃并披露计数）
- **`stats.geodetector_risk`** 地理探测器·风险探测器（`native`·成熟度 已验证，契约: `geodetector_risk_analysis`，出处: `wang2010`）
  - 假设：逐分层对均值差的 Welch t 检验（equal_var=False，方差不等稳健）；permutations>0 附固定种子 42 标签置换双侧 p（(count+1)/(perms+1)）；方向判定 p<0.05 才给 higher/lower，否则 not_significant
  - 局限：两分层的均值差不构成因果证据；分层数<2 时该对的 t/p/方向不可得（not_significant + note）；多重比较未校正：对数随分层数平方增长，解读需谨慎

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
  - 假设：裁剪在 WGS84 工作帧做纯拓扑相交（不量度）；面裁剪面/面裁点：输入几何有效性由上游校验
  - 局限：无效自交多边形先 make_valid（披露）；拓扑输出不保证面积/长度语义（工作帧非投影）

## `geometry_dissolve` — 融合/溶解

同属性面融合。

- **`geometry.dissolve`** 融合溶解（`native`·成熟度 —）
  - 假设：按字段 dissolve 后 unary_union（纯拓扑，不量度）
  - 局限：无效几何先 make_valid（披露）；属性只保留分组键

## `geometry_overlay` — 几何叠加

GEOS 拓扑叠加（intersection/union/difference 等），纯拓扑不量度。

- **`geometry.overlay`** 几何叠加（`native`·成熟度 已验证）
  - 假设：GEOS 精确拓扑叠加（intersection/union/difference/symmetric_difference/identity）；叠加在 WGS84 工作帧执行：图层 CRS 不一致时先对齐到 layer_a；结果属性 = 两图层属性列的并集（gpd.overlay 语义）
  - 局限：纯拓扑运算：叠加输出坐标仍是度，叠加面积须另投影后量测；输入几何经 make_valid 修复（无效多边形可能改变边界形状）；面×点叠加结果是点集（输出按 polygon_feature_set 声明以面×面为主）

## `geostatistical_simulation` — 地统计模拟

条件高斯多实现模拟（SGS）：P10/P50/P90/std ensemble，风险制图与不确定性传播；caller_seeded 可复现。

- **`interpolation.sgs`** SGS 条件高斯模拟（`native`·成熟度 已验证，契约: `sgs_analysis`，出处: `goovaerts1997`，精度: sampling）
  - 假设：Goovaerts 1997 标准流程：normal-score 域沿随机路径逐节点条件 SK，条件集 = k 近邻原始样本 + k 近邻已模拟节点；caller_seeded：单一 PCG64 流（路径+噪声同源），同 seed 逐位复现；ensemble 统计（P10/P50/P90/std）来自真实多实现——非解析方差面
  - 局限：蒙特卡洛近似：实现数有限时分位数有采样误差（R≥100 推荐用于分位数）；高斯性假设经 normal-score 秩变换近似成立——非高斯依赖结构未建模；病态邻域回退条件值经验抽样（与 OK 邻域均值回退同口径）
  - 回退：`interpolation.kriging`→approximation
  - 资源包络：像元硬上限 20000000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-09，atol=0

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

- **`spatial.grid.h3`** H3 六边形聚合（`native`·成熟度 已验证）
  - 假设：H3 分辨率显式参数；计数/数值聚合（sum/mean）显式声明；点落格按 H3 索引包含关系
  - 局限：跨分辨率的单元面积不同（对比需归一化，披露）
  - 回退：`spatial.grid.fishnet`→approximation
- **`spatial.grid.fishnet`** 渔网格网聚合（`native`·成熟度 已验证）
  - 假设：等矩网格（目标 CRS 米制格宽）；计数/数值聚合显式
  - 局限：格网在投影平面定义（高纬变形与投影一致，披露）
  - 回退：`spatial.grid.h3`→approximation

## `gwr` — 地理加权回归 (GWR)

空间非平稳性探测：逐观测局地 WLS（bisquare 自适应核）。

- **`spatial.gwr`** 地理加权回归（GWR）（`native`·成熟度 已验证，契约: `gwr_analysis`，出处: `brunsdon1996`, `fotheringham2002`）
  - 假设：自适应 bisquare 核，带宽=最近邻数 k（默认 30，钳制 [5,n/2]）；bandwidth_selection=cv 时在有界网格上留一 CV（确定性穷举）；fixed 路径产以 k 为中心的 3 点带宽敏感性摘要（ENP/R²，F-4）
  - 局限：局部共线性会让局部系数失真（全局 VIF 不代表局部）；局地 SE/t 不含量化带宽选择与核形态的不确定性；AICc 没有唯一公认公式——比较带宽/模型时保持同一实现
- **`spatial.mgwr`** 多尺度地理加权回归（MGWR）（`native`·成熟度 已验证，契约: `mgwr_analysis`，出处: `fotheringham2017`, `fotheringham2002`, `brunsdon1996`）
  - 假设：每个设计列（含截距项）独立带宽的 bisquare kNN 反向拟合；联合 GWR 解热启动；逐项部分残差 + LOO-CV 带宽搜索（≤20 候选）；ENP=逐项帽矩阵对角迹之和；AICc 用 q=ENP+1 高斯近似
  - 局限：反向拟合是不动点迭代：收敛到局部最优，不保证全局最优；带宽为有界网格穷举而非连续优化；等带宽锚在精确可表示表面上逐位成立，噪声数据的等带宽解与 GWR 有平滑交互偏差；局部共线性会让局部系数失真；AICc 无唯一公认公式

## `hotspot` — 热点显著性分析

Getis-Ord Gi* 等空间聚类显著性检验。

- **`spatial.hotspot.local`** 局部热点显著性（Getis-Ord Gi*）（`native`·成熟度 已验证，契约: `gi_star_analysis`，出处: `getis_ord1992`）
  - 假设：Gi* 含 w_ii=1（distance band 内二值权重，含自身）；significance_method=normal：解析正态 p（既有路径，输出键不变）；significance_method=permutation：条件随机化置换 p（固定种子 42，双侧 (count+1)/(perms+1)；全局矩取观测值，邻域值随机重排）
  - 局限：正态近似在小样本/偏态分布下 p 值偏乐观（置换路径可对照）；置换路径 n>5000 拒绝；邻居样本为全多重集无放回抽取（与严格 y_{−i} 条件化差一项，Monte-Carlo 近似）；逐格检验的多重比较问题由 BH-FDR 缓解而非消除

## `ica_transform` — ICA 独立成分分析

FastICA 独立成分分解（Hyvärinen 1999；random_state=42，whiten=unit-variance；收敛性显式披露）。

- **`remote.ica`** 独立成分分析（FastICA）（`native`·成熟度 已验证，契约: `ica_analysis`，出处: `hyvarinen1999`）
  - 假设：源信号统计独立且非高斯（FastICA 负熵代理）；whiten=unit-variance；random_state=42 固定（fixed_seed）；收敛性显式披露不静默；公共有效掩膜；n_valid ≥ max(8, k+2)（whiten 数值下限）
  - 局限：分量序与符号不唯一（ICA 固有）——跨运行比较需固定实现版本；未收敛（max_iter 内）→ converged=false 披露，分量非稳定估计

## `image_segmentation` — 图像分割

k-means 分割基座（Lloyd 1982；光谱 z-score + 加权空间坐标特征，random_state=42 确定性；非 SLIC——无几何紧致约束/watershed 精化，披露）。

- **`remote.segmentation`** 图像分割（k-means 基座）（`native`·成熟度 已验证，契约: `segmentation_analysis`，出处: `lloyd1982`）
  - 假设：特征 = 标准化光谱波段 + 归一化坐标·spatial_weight·compactness；KMeans(random_state=42, n_init=10)（确定性；Lloyd 1982 惯用法）；段数 > 有效像元数 → 钳制并披露（realized < requested）
  - 局限：flat-color k-means 基座，非 SLIC 超像素：compactness 只是空间特征权重乘子（无几何紧致约束）、无 watershed 精化（诚实边界）；常量波段不进特征（剔除披露）；全常量 → 仅按坐标分割

## `indicator_kriging` — 指示克里金

逐阈值指示克里金（Journel 1983）：P(Z≤t) 概率面 + p50 阈值面 + 可选 E-type 估计。

- **`interpolation.indicator_kriging`** 指示克里金（`native`·成熟度 已验证，契约: `indicator_kriging_analysis`，出处: `journel1983`, `matheron1963`，精度: exact）
  - 假设：逐阈值指示变换 I=1[z≤t] → 各自经验变异函数 + 拟合 → 指示场普通克里金；variogram_model=auto 时逐阈值在全部 6 家族里按加权 RSS 选型（逐阈值披露）；概率钳制 [0,1]：被钳制格数逐格计数（绝不静默）
  - 局限：逐阈值独立克里金不保证概率面在阈值间单调（P(Z≤t) 单调性未强制，已披露）；常量指示场（阈值在样本值域之外）输出常量概率（无变异函数拟合）；E-type 类内分布未建模——不是分位数中值的精确期望
  - 回退：`interpolation.kriging`→approximation
  - 资源包络：24B/要素，对预算 200000，要素硬上限 500000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-06，atol=1e-09

## `interpolation_model_selection` — 插值模型选择

以 LOOCV/CV 证据比较可行插值方法并确定性推荐（RMSE 排名）。

- **`interpolation.model_compare`** 插值模型比较（`native`·成熟度 已验证，契约: `interpolation_model_compare`，出处: `shepard1968`, `matheron1963`）
  - 假设：以各方法库级 LOOCV/CV 证据（RMSE）排名——方法选择基于证据而非惯例；固定方法序 + 预算序贯走查：完全确定性（同输入必同表）；样本下限：idw≥2/tin≥4/trend≥6/rbf≥3/kriging≥20（各方法 CV 下限）
  - 局限：CV 证据是样本内泛化估计——极端外推场景不外推结论；cv_budget 预算走查按固定方法序跳过后续方法（跳过原因逐行披露）；比较运行于样本（无表面输出）；最终表面仍需调用对应插值工具
  - 回退：`interpolation.idw`→approximation
  - 资源包络：32B/要素
  - 取消：none
  - 数值容差：rtol=1e-09，atol=0

## `join_count_statistics` — Join Count 统计

二值场的邻接同异类连接计数检验（Cliff-Ord free sampling）。

- **`stats.bivariate_join_count`** 双色 Join Count（二类别空间关联）（`native`·成熟度 已验证，契约: `bivariate_join_count_analysis`，出处: `cliff_ord1973`）
  - 假设：字段恰好取两个值（任意数值类别，违者 UnsupportedMethod/DegenerateData）；按排序映射 B=较小值 / W=较大值；二值对称权重；n_BB（同类）/n_BW（异类）/n_WW 按无序连接计数；期望/方差用 non-free sampling（Cliff-Ord 1973，条件于类别边际的解析矩），n≥4；忽略权重结构细节（披露于结果）
  - 局限：non-free sampling 是零假设近似，不反映真实抽样设计；knn 权重是邻接的近似；queen/rook 需要面要素；小 n 下解析 z 的正态近似偏乐观（置换可对照）
- **`stats.join_count`** Join Count（二值空间关联）（`native`·成熟度 已验证，契约: `join_count_analysis`，出处: `cliff_ord1973`, `moran1950`）
  - 假设：字段必须 ⊆ {0,1}，含 0 与 1 两个类（违者 UnsupportedMethod）；二值对称权重；n_BB/n_BW/n_WW 按无序连接计数；期望/方差用 non-free sampling（Cliff-Ord 1973，条件于类别边际的解析矩），n≥4（审计 F-2：与实现口径同步）
  - 局限：non-free sampling 忽略权重结构细节（只含连接数 J）；knn 权重是邻接的近似；queen/rook 需要面要素；小 n 下解析 z 的正态近似偏乐观

## `kde_density` — 核密度估计

KDE 连续密度面/等值线（定量密度表达）。

- **`spatial.kde.contours`** 核密度等值线（`native`·成熟度 已验证）
  - 假设：KDE 表面的 marching-squares 等值线（matplotlib Agg）
  - 局限：等值线级别为渲染选择（非分位数语义）
  - 回退：`spatial.kde.surface`→equivalent
- **`spatial.kde.surface`** 核密度全格网表面（`native`·成熟度 已验证，契约: `kde_surface_analysis`，出处: `silverman1986`, `abramson1982`）
  - 假设：Silverman 规则或显式带宽（scipy gaussian_kde）；点数上限触发时降级披露；bandwidth_method=fixed（默认）：单一各向同性带宽，行为与历史逐位一致
  - 局限：高斯核假设；大规模点集走聚合通道（fallback 已声明）；adaptive 为一步先导近似（非迭代变带宽）；先导带宽与λ 范围随结果披露；自适应评估与固定路径同阶 O(n·grid)，点数上限同 #384
  - 回退：`spatial.kde.contours`→equivalent

## `local_gearys_c` — 局部 Geary's C

局部相似性/相异性检测（Local Geary's C_i，Anselin 1995；与 LISA 的方向配对互补）。

- **`stats.local_geary`** 局部 Geary's C（相似性/相异性）（`native`·成熟度 已验证，契约: `local_geary_analysis`，出处: `anselin1995`, `geary1954`, `holm1979`, `benjamini_hochberg1995`）
  - 假设：C_i=Σ_j w_ij(z_i-z_j)²，z 为总体方差标准化（esda.Geary_Local 同式）；行标准化权重；置换检验固定种子 42、双侧 (count+1)/(perms+1)；多重校正默认 BH-FDR（可 bonferroni/holm/none）
  - 局限：Local Geary 只判相似/相异，高-低方向配对用 LISA（h3_lisa）；±1 二值场等离散取值下置换分布退化，p 分辨率受格子限制；逐格校正后 α=0.05 判定在随机数据下仍有 ~0.05q 假显著期望

## `local_join_count` — 局部 Join Count

二值场的逐位置共位簇检测（Anselin & Li 2019；全局 Join Count 的局部对应物）。

- **`stats.local_join_count`** 局部 Join Count（二值共位簇）（`native`·成熟度 已验证，契约: `local_join_count_analysis`，出处: `anselin_li2019`, `sokal1998`, `benjamini_hochberg1995`）
  - 假设：y ⊆ {0,1}（违者 UnsupportedMethod）；二值对称权重（无自环）；LJC_i=Σ_j w_ij·I(y_i=1)·I(y_j=1)；y=0 位置 LJC≡0、p≡1；条件置换推断（保持 1 的总数），单侧上尾 (count+1)/(perms+1)
  - 局限：只检测 y=1 的共位聚集；y=0 的聚集用 0/1 翻转后再检；BH 在全 n 位置上校正（含 y=0 的 p≡1），对稀疏 1 偏保守；knn/distance_band 权重是邻接的近似；queen/rook 需要面要素

## `local_morans_i` — 局部莫兰/LISA

局部热点/冷点聚类。

- **`stats.h3_lisa`** H3 LISA 局部自相关（`native`·成熟度 已验证，出处: `anselin1995`）
  - 假设：esda.Moran_Local（Queen 邻接、行标准化、seed=42）；孤岛格网给中性结果（p=1、q=0），保持行对齐（#927）；输入为带数值字段的 H3 网格（如 h3_binning 产物）
  - 局限：逐格 p_sim<0.05 在随机数据下期望产出 ~0.05n 假显著（结果内披露期望数）；H3 分辨率改变邻接结构，跨分辨率结果不可比

## `location_allocation` — 区位配置

设施选址-分配优化（tier-3 门控）。

- **`network.location_allocation`** 区位配置（`native`·成熟度 已验证，契约: `location_allocation_analysis`，出处: `teitz_bart1968`, `hakimi1964`）
  - 假设：p_median 目标 = 最小化 Σ w_i·min_{j∈S} C_ij；max_coverage = 最大化 cutoff 内覆盖需求权重；p_center（Foundation V2 A4，Hakimi 1964 max-min）= 最小化可达需求的最大服务成本（打平按总加权成本次级判据）；代价矩阵 = 路网 OD 行程时间（不可达 = inf，参与目标时按 1e9 惩罚）
  - 局限：启发式 >20k 组合；exact ≤20k —— C(m,p) 枚举在预算内给出精确最优，超出切 Teitz-Bart 顶点替换 / 贪婪覆盖 / p-center 贪婪+顶点替换（≤10 轮）；max_coverage 贪婪覆盖是次模函数的经典贪婪：有 (1−1/e)≈0.632 近似保证（无数据相关最坏界更差），非精确最优 —— 精确路径走 solver=exact_milp（network.mclp_exact）；不可达需求点列入 summary.unassigned_ids（不参与选址目标）
- **`network.pcenter_exact`** p-中心精确求解（MILP）（`native`·成熟度 已验证，契约: `location_allocation_analysis`，出处: `hakimi1964`）
  - 假设：Big-M 0/1 MILP：min z；z ≥ c_if·x_if − BigM(1−x_if)；Σ_f x_if=1；x_if≤y_f；Σ_f y_f=p（BigM=最大有限代价）；Hakimi 1964 max-min 目标：最小化可指派需求的最大服务成本；不可达需求不参与 max 目标（inf 不是服务成本）、进 summary.unassigned_ids —— 与既有 p-center 语义一致
  - 局限：规模闸：需求×候选 ≤ 25000 且候选 ≤ 500 —— 超限抛 ResourceScaleMismatch 指向启发式路径（不静默回退）；MILP 主目标仅 z（max 服务成本）；打平时的次级总加权成本仅作披露，不进入最优化；最优解不唯一时由 HiGHS 确定性给出其一；最优目标值不受影响
  - 回退：`network.location_allocation`→approximation
- **`network.pmedian_exact`** p-中位精确求解（MILP）（`native`·成熟度 已验证，契约: `location_allocation_analysis`，出处: `revelle_swain1970`, `hakimi1964`）
  - 假设：0/1 MILP 精确式：min Σ w_i·c_if·x_if；Σ_f x_if=1 ∀可指派需求；x_if≤y_f；Σ_f y_f=p；求解后端 scipy.optimize.milp（HiGHS 分支定界）：固定输入确定性复现；代价矩阵 = 路网 OD 行程时间；不可达对从模型剔除（不引入 1e9 惩罚近似）
  - 局限：规模闸：需求×候选 ≤ 25000 且候选 ≤ 500 —— 超限抛 ResourceScaleMismatch 指向启发式路径（不静默回退）；全程不可达需求点进 summary.unassigned_ids（不参与目标，与启发式语义一致）；最优解不唯一时由 HiGHS 确定性给出其一；最优目标值不受影响
  - 回退：`network.location_allocation`→approximation
- **`network.mclp_exact`** 最大覆盖精确求解（MILP）（`native`·成熟度 已验证，契约: `location_allocation_analysis`，出处: `church_revelle1974`）
  - 假设：0/1 MILP 精确式（Church & ReVelle 1974 MCLP）：max Σ w_i·z_i；z_i ≤ Σ_{j∈N_i} y_j；Σ_f y_f=p；N_i={j: c_ij ≤ cutoff}；覆盖半径 cutoff 以活动阻抗为单位（默认行程时间秒，缺省 900s=15min，与启发式路径同缺省）；求解后端 scipy.optimize.milp（HiGHS 分支定界）：固定输入确定性复现
  - 局限：规模闸：需求×候选 ≤ 25000 且候选 ≤ 500 —— 超限抛 ResourceScaleMismatch 指向启发式路径（不静默回退）；无候选落入 cutoff 的需求点不可能被覆盖：不进模型、进 summary.unassigned_ids（与枚举/启发式语义一致）；最优解不唯一时由 HiGHS 确定性给出其一；最优覆盖权重不受影响
  - 回退：`network.location_allocation`→approximation

## `mantel_test` — Mantel 时空检验

Mantel 检验（1967）——空间距离矩阵与时间距离矩阵的相关（标准化 Mantel r），时间标签置换 p 值。

- **`point_pattern.mantel`** Mantel 时空距离相关检验（`native`·成熟度 已验证，契约: `mantel_analysis`，出处: `mantel1967`）
  - 假设：标准化 Mantel r = Pearson(上三角空间距离, 上三角时间距离)；时间标签置换（固定种子 42）构成零假设分布；alternative=greater（聚集方向，缺省）/ two-sided
  - 局限：Mantel 把全部点对当独立样本（距离矩阵非独立），对空间自相关敏感——meta 中 disclosure 披露；密集 n×n 距离矩阵：n ≤ 2000 诚实上限（超限结构化拒绝）；p 值分辨率 1/(permutations+1)，上限 999

## `mcda_evaluation` — 多准则决策评价

候选方案×准则×约束的 MCDA 评价（WSM/TOPSIS + Pareto + 敏感性）。

- **`decision.mcda.wsm`** MCDA 决策评价（WSM/TOPSIS）（`native`·成熟度 已验证，出处: `hwang_yoon1981`）
  - 假设：权重/准则方向由声明给定；蒙特卡洛不确定性仅在声明不确定参数时激活
  - 局限：不合成证据：无不确定参数时不注入伪噪声分布

## `mnf_transform` — MNF 变换

最小噪声分数变换（Green 1988：局部差分噪声白化 + 白化空间 PCA，分量按 SNR 排序，含逆变换去噪重建）。

- **`remote.mnf`** 最小噪声分数变换（MNF）（`native`·成熟度 已验证，契约: `mnf_analysis`，出处: `green1988`）
  - 假设：噪声协方差由水平/垂直一阶差分估计（(C_h+C_v)/4，差分加倍校正披露）；白化空间噪声方差=1，SNR_i = λ_i − 1（λ 为白化 PCA 特征值 ddof=1）；公共有效掩膜：任一波段无效 → 整行剔除（非 pairwise-complete）
  - 局限：无流式实现：n_bands·H·W ≤ 16M 像元，超限先拒绝；常量/共线波段使噪声协方差奇异 → DegenerateData（诚实拒绝）；分量/载荷代数符号依 LAPACK 约定（同一构建内稳定）

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
  - 局限：edge_correction=none（缺省）为原始估计——边界点低估 G/F；V3 起可选 border（reduced-sample）/isotropic（Ohser 加权）；border 校正要求焦点/查询点到四边距离 > r_max（内点不足时诚实拒绝）；J 在 F(r)→1 时分母退化记 NaN（j_undefined_from 披露）

## `network_centrality` — 网络中心性

路网节点中心性（度/接近/介数/边介数）：精确 Brandes 与固定种子采样两种实现变体，规模护栏先行。

- **`network.centrality`** 网络中心性（`native`·成熟度 已验证，契约: `network_centrality_analysis`，出处: `brandes2001`）
  - 假设：度数 = 入度+出度（DiGraph 语义：单行路段计数不对称，如实呈现）；接近/介数以边权为距离最小化（travel_time_s 秒 / length_m 米），非跳数；介数 Brandes 精确 n≤2000；n>2000 切 k=500 固定种子 42 采样（betweenness_mode 披露）
  - 局限：节点上限 20000（计算前 ResourceScaleMismatch 显式拒绝，不 OOM）；edge_betweenness 仅边数≤1500 精确；超出诚实拒绝（不做假采样）；逐节点输出上限 5000 行（按主指标降序裁剪，output_rows_trimmed 披露）
- **`network.eigenvector_centrality`** 特征向量中心性（`native`·成熟度 已验证，契约: `network_centrality_analysis`，出处: `bonacich1972`）
  - 假设：Bonacich 1972 主特征向量中心性：A·x=λx，稀疏幂迭代（scipy CSR matvec）+ L2 归一；收敛判据 L1 增量 < tol（缺省 1e-10）；实际迭代数/达成增量在 meta/summary 披露；DiGraph 取左特征向量（入边语义，与 networkx 一致）：度量被高分层节点指向的程度
  - 局限：负边权拒绝（UnsupportedMethod）：幂迭代依赖 Perron-Frobenius 非负前提，不做移位/取绝对值变通；不连通图照常迭代：得分反映谱半径最大（主导）分量，谱半径并列时为主导向量混合 —— meta 显式披露；max_iter 内未收敛不报错：converged=False + 实际迭代数/达成增量披露；孤立节点恒 0

## `od_flow_mapping` — OD 流向图

把 OD 对（坐标+权重）构建为有界流向线要素层。

- **`flow.od_arc_build`** OD 流向构建（`native`·成熟度 已验证）
  - 假设：OD 对 → 有界带权流向线（宽度映射显式参数，ADR-0092 D）
  - 局限：线宽是渲染量（非线性量纲）——地图模型 flow_od_arc 消费

## `od_matrix` — OD 成本矩阵

多起点×终点网络成本矩阵。

- **`network.od_matrix`** OD 成本矩阵（`native`·成熟度 已验证，契约: `network_od_matrix`，出处: `dijkstra1959`）
  - 假设：每个唯一起点一趟累积式 Dijkstra（#449），距离/时间沿同一最短路树累积（GIS-19）；cutoff_s 以活动阻抗为单位（秒/米）；超出预算的对以 reachable=False + inf 返回，绝不静默缺行；有向图语义：单行路网下 OD(A→B) ≠ OD(B→A)
  - 局限：OD 树代价不含转向惩罚（树无路径上下文，#455 跨工具语义）；起点/终点捕捉在 500 m 容差内静默吸附最近边；捕捉距离在结果 snap_evidence 中逐端点披露

## `pair_correlation_function` — 成对相关函数

成对相关函数 g(r)=K′(r)/(2πr)（Illian 2008）——随半径的聚集/规则尺度谱，配固定种子 CSR 包络。

- **`point_pattern.pcf`** 成对相关函数 g(r)（`native`·成熟度 已验证，契约: `pcf_analysis`，出处: `illian2008`, `ripley1976`）
  - 假设：g(r)=K′(r)/(2πr)：由各向同性校正 K 的离散导数 + Epanechnikov 平滑；bandwidth（米）缺省 0=一个 r 步宽（自动值在输出披露）；CSR 参考 g≡1；g>1 聚集 / g<1 规则
  - 局限：g 由 K 的离散导数间接估计，r 网格粒度限制分辨率；Epanechnikov 平滑带宽敏感：小带宽噪声大、大带宽抹平峰值（< 半个 r 步宽类型化拒绝 DegenerateData——R10-guard，杜绝 NaN 进输出）；O(n²) 成对统计，上限 2 万点（超出诚实拒绝）

## `poi_query` — POI 要素获取

按范围/类别获取点要素（本地优先，在线兜底）。

- **`poi.query.local`** POI 查询（本地优先）（`native`·成熟度 —）
  - 假设：本地数据优先（本地索引），不做几何改写
  - 局限：查询结果依赖本地数据完整性（元数据披露来源）
- **`poi.area_search`** 区域 POI 检索（`native`·成熟度 实验）
  - 假设：范围检索（在线 POI 服务）——external API 客户端；结果内容/排序由服务方决定（本库不重排）
  - 局限：deterministic=False：在线结果可变（已声明）；服务配额/风控可能拒绝（结构化错误返回）

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

- **`profile.spatial.stats`** 空间数据画像（`native`·成熟度 已验证）
  - 假设：画像为描述性统计（计数/几何/字段元数据），不产出新几何
  - 局限：字段类型推断是启发式（数值/类别判定规则披露于工具层）

## `proximity_buffer` — 邻近缓冲

距离缓冲区生成。

- **`spatial.buffer.proximity`** 距离缓冲区（`native`·成熟度 已验证）
  - 假设：距离单位为米：实现经 to_utm_gdf 自动投影到局部 UTM
  - 局限：跨带 UTM 投影失真记入 transformations 披露；缓冲段数固定惯例（圆滑度有限）

## `radiometric_normalization` — 辐射归一化

稳健跨波段/跨场景归一化（2-98 分位拉伸或参考场景分位匹配；NaN-aware 分位、逐波段分位披露；线性增益/偏移不变）。

- **`remote.robust_normalize`** 稳健波段/场景归一化（`native`·成熟度 已验证，契约: `robust_normalize_analysis`）
  - 假设：percentile_stretch：逐波段 [2,98] 分位（可调）线性拉伸到 [0,1]；percentile_match：源分位拉伸后重缩放到参考栈同序波段分位区间；NaN-aware 分位（np.nanpercentile）；逐波段所用分位完整披露
  - 局限：对线性增益/偏移不变——非线性辐射差异（直方图形状）不校正；常量波段（分位区间 0）→ DegenerateData；绝对辐射语义不保留；相对归一化：非伪不变目标（PIF）/直方图匹配全量实现

## `raster_change_detection` — 双时相栅格变化检测

两个栅格工件的对齐像元级变化检测（差值/绝对差/归一化差 + 阈值分类）。

- **`remote.change.raster`** 双时相栅格变化检测（`native`·成熟度 已验证）
  - 假设：A（T1）网格为基准，B 经 WarpedVRT 对齐；对齐事实进质量证据；有效像元 = 双方都有效（任一 nodata → nodata）；差值/绝对差为逐像元辐射差，不构成语义分类
  - 局限：差值法对配准/辐射差异敏感，无语义分类（变化≠地类转移）；normalized_difference 零分母 → nodata（不产 inf）；无云/阴影 QC（跨期云污染进入差值，见 remote.cloud_qc 基础）
- **`remote.cva`** 变化向量分析（CVA）（`native`·成熟度 已验证，出处: `malila1980`）
  - 假设：两景波段按语义角色对齐（缺角色拒绝，不按位置猜测）；幅度=全角色欧氏范数；角度=固定角色序前两分量 atan2（弧度）；同一像元任一角色任一期无效 → 输出 NaN
  - 局限：CVA 只给幅度/方向，不构成土地覆盖语义变化；方向角依赖角色序约定——跨研究比较需披露所用角色序
- **`remote.mad_change`** MAD / IR-MAD 变化检测（`native`·成熟度 已验证，契约: `mad_change_analysis`，出处: `nielsen1998`）
  - 假设：两期栈各自标准化 → SVD-CCA → MAD_i = a_i·X − b_i·Y（ρ 升序）；χ² 栅格自由度 = k=n_bands（标准化变分量方差 2(1−ρ_i) → 每分量 1 dof，Nielsen 1998/Canty χ²_k 惯例）；ρ 钳制 ≤1−1e-12（恒等场景防 0/0）；IR-MAD 权重 w=1/χ²（均值归一 + 下限 1e-4），固定点迭代 ≤10
  - 局限：对逐波段线性辐射偏移/增益不变（标准化吸收）——检测结构变化；完整 IR-MAD 的 no-change 概率优化未实现（简化重加权披露）；波段共线/常量 → DegenerateData（CCA 要求满秩场景协方差）
- **`remote.ratio_change`** 双时相比值变化（`native`·成熟度 已验证，契约: `ratio_change_analysis`）
  - 假设：比值法适用于 SAR 后向散射/强度（同量纲输入）；ratio：a/b，零分母→NaN；log_ratio：log(a)−log(b)（对数域对称）
  - 局限：比值不区分变化原因（物候/几何/定标漂移同权混合）；log_ratio 输入须为正（线性强度或 dB）

## `raster_cog_conversion` — COG 栅格转换

单文件 GeoTIFF → Cloud Optimized GeoTIFF：分块重排 + 多级概视图（金字塔重采样）+ footer 索引，产出可流式范围读取的云原生栅格。

- **`raster.cog.convert`** Cloud Optimized GeoTIFF 转换（`native`·成熟度 —）
  - 假设：概视图金字塔重采样（nearest），footer 索引按 COG 规范
  - 局限：单文件 GeoTIFF 输入；已有 COG 结构则直接通过

## `raster_dimensionality_reduction` — 波段降维（PCA）

多波段栅格 SVD 主成分分析（协方差/相关 PCA、explained variance、载荷与前 k 分量栅格）。

- **`remote.pca`** 波段栈 PCA（SVD 降维）（`native`·成熟度 已验证，契约: `raster_pca_analysis`）
  - 假设：标准 SVD/PCA 无单一经典出处声明——method_references 诚实留空；公共有效掩膜：任一波段无效 → 整行剔除（非 pairwise-complete）；standardize=False 协方差 PCA / True 相关矩阵 PCA（方差 ddof=1）
  - 局限：无流式实现：n_bands·H·W ≤ 16M 像元，超限先拒绝（不假装可扩展）；载荷符号不唯一（SVD 符号约定）——跨运行比较需固定实现版本

## `raster_reclassify` — 栅格重分类

连续栅格值按方案映射为离散类别。

- **`raster.reclassify.rule`** 规则重分类（`native`·成熟度 已验证）
  - 假设：规则表逐段左闭右开映射；未命中段 → nodata（披露）
  - 局限：浮点边界比较语义（无容差）——由规则表作者负责

## `raster_resample` — 栅格重采样

改变像元大小和/或 CRS（对齐预处理）。

- **`raster.resample.grid`** 网格重采样/重投影（`native`·成熟度 已验证）
  - 假设：重采样方法（邻近/双线性/平均）显式声明；目标网格由对齐参数决定（WarpedVRT）
  - 局限：重投影经 GDAL/PROJ；极区/跨子午线由 Warp 处理（披露）

## `raster_source` — 栅格数据源

DEM/遥感栅格获取。

- **`raster.source.dem`** DEM 栅格获取（`native`·成熟度 实验）
  - 假设：DEM 拉取（Copernicus 30m）经 STAC/非交互通道
  - 局限：在线数据源：可用性与产品版本不受本库控制（external）

## `rate_aggregation` — 率/密度聚合

显式分母的逐区归一化：分子（字段求和/计数）÷ 分母（区字段/真实面积/要素计数）；count 聚合不是率/密度，分母缺失/≤0 的区不产率值（rate=null）。

- **`spatial.aggregate.rates`** 显式分母聚合（率/密度）（`native`·成熟度 实验，契约: `aggregate_with_denominator`）
  - 假设：分子 = 分子字段按区求和（NaN 值剔除并披露）或缺省的要素计数；分母三种口径：区分母字段（field）/ 区真实面积 m²（area）/ 要素计数（count）；率 = 分子 ÷ 分母；面积分母在 UTM/极方位度量 CRS 下计算（Web Mercator 不可信）
  - 局限：分母通道已接入 spatial_aggregate 工具（denominator_kind/numerator_field/denominator）——需中央接线 numerator_field/denominator_kind/denominator_field 三个参数；count 分母的输出是比值（count_ratio_not_rate），不是率/密度；分母缺失/≤0 的区 rate=None（JSON null）——从不编造 0 或 inf

## `rate_smoothing` — 经验贝叶斯率平滑

计数/人口率的经验贝叶斯收缩平滑（Marshall 1991 MOM 先验；全局或邻居先验；零人口区不产率值）。

- **`stats.rate_smoothing`** 经验贝叶斯率平滑（Marshall 1991 MOM）（`native`·成熟度 已验证，契约: `rate_smoothing_analysis`，出处: `marshall1991`）
  - 假设：分子=观测计数、分母=风险人口；原始率 r_i=C_i/P_i；先验均值/方差用矩估计（MOM，Marshall 1991）：假设计数近似 Poisson；weights_scheme 给定时先验来自邻居（不含自身）的人口加权矩（局部 EB）；缺省全局 EB
  - 局限：MOM 先验假设 Poisson 计数——小计数/超散布数据下收缩失真；零人口区不产率值（类型化排除并披露），不是 0；孤岛（无有效邻居）保留原始率并披露；极端收缩不等于因果调整

## `regression_kriging` — 回归克里金

OLS 趋势（协变量）+ 残差克里金的混合插值（Odeh 1995）。

- **`interpolation.regression_kriging`** 回归克里金（`native`·成熟度 已验证，契约: `regression_kriging_analysis`，出处: `odeh1995`, `matheron1963`，精度: approximate）
  - 假设：RK = OLS 趋势（z ~ 协变量）+ 残差普通克里金（auto 变异函数）；目标处协变量值由样本协变量经 IDW（k=5, power=2）近似——approximate 语义；rk_variance 仅含残差克里金方差；趋势系数不确定性未传播（如实披露）
  - 局限：协变量场在目标处不可知——IDW 近似误差进入趋势项（approximate=True）；常量协变量（零方差）结构化拒绝（DegenerateData）；至少 2 个协变量；EPSG:3857 工作 CRS 的 Web Mercator 尺度畸变（与克里金同）
  - 回退：`interpolation.kriging`→approximation
  - 资源包络：24B/要素，对预算 200000，要素硬上限 500000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-06，atol=1e-09

## `route_optimization` — 路线优化

多站点访问顺序优化（VRP，tier-3 门控）。

- **`network.route_optimization`** 路线优化（`native`·成熟度 已验证）
  - 假设：最近邻初始巡游 + 2-opt 局部搜索改进（有向代价矩阵，方向翻转计价 #540）；leg 代价 = 活动阻抗下的路网最短路（OD 树重建，无逐 leg 重复寻路）
  - 局限：NN+2-opt 启发式非精确 TSP：解无最优性保证（迭代上限 100）；不可达 leg 计 1e9 代价（巡游仍连贯，总代价如实累加 inf leg）
- **`network.optimize_route`** 路线优化（VRP）（`native`·成熟度 已验证）
  - 假设：最近邻初始巡游 + 2-opt 局部搜索改进（有向代价矩阵，方向翻转计价 #540）；leg 代价 = 活动阻抗下的路网最短路（OD 树重建）
  - 局限：NN+2-opt 启发式非精确 TSP：解无最优性保证（迭代上限 100）；stops 上限 200（工具层显式拒绝超限，2-opt 超线性）

## `rx_anomaly_detection` — RX 异常检测

Reed-Xiaoli 全局 RX 异常检测（Mahalanobis 距离 + 尺度不变岭正则；单高斯背景假设，阈值启发式披露；局部/核 RX 未实现）。

- **`remote.rx_anomaly`** RX 全局异常检测（`native`·成熟度 已验证，契约: `rx_analysis`，出处: `reed1990`）
  - 假设：δ(x)=√((x−μ)ᵀΣ_r⁻¹(x−μ))；Σ_r = Σ + regularize·(tr Σ/k)·I；单高斯全局背景假设（局部 RX/核 RX 未实现，披露）；阈值 mean(δ)+k·σ(δ) 为启发式建议（k 显式参数，非假设检验）
  - 局限：≥3 波段推荐（2 波段可运行但背景估计弱）；常量场 → δ=0 披露；异常≠语义目标——δ 高只说明偏离全局统计

## `sar_analysis` — SAR 时序/极化分析

SAR 时序栈统计（含 CV/鲁棒分位数）、时序合成、VV/VH 极化比与双时相对数比值（滤波/定标为独立能力：sar_speckle_filtering / sar_radiometric_calibration）。

- **`sar.temporal_composite`** SAR 时序栈合成（mean/median/percentile）（`native`·成熟度 已验证，契约: `sar_temporal_composite_analysis`，出处: `oliver_quegan1998`）
  - 假设：时间维聚合为描述性合成（median 为斑点拖尾下的鲁棒惯用）；nodata/NaN 逐切片剔除；全切片无效像元 → NaN（披露）；acquisitions（可选）=每切片获取元数据（极化/日期/入射角/轨道向）→ 可比性检查：入射角差>5°/升降轨混搭/极化混搭 → 证据块 warnings（披露级，不拒绝；缺省不做可比性判断）
  - 局限：无滤波/定标隐式前置（独立原生算法见 sar.speckle_filter 等）；栈深 ≤24、H·W ≤4096×4096，超限 ResourceScaleMismatch 先拒绝
- **`sar.temporal_stats`** SAR 时序栈统计（`native`·成熟度 已验证，契约: `sar_temporal_stats_analysis`）
  - 假设：输入假定已几何校正并对齐；std 为总体标准差（ddof=0）；nodata/NaN 逐切片剔除，剩余有效切片上统计（部分有效像元披露）；CV=std/mean（可选）：|mean|≤1e-12 → NaN；dB 域 CV 无物理量纲（披露）
  - 局限：本工具无滤波/定标隐式前置——独立原生算法见 sar.speckle_filter/sar.radiometric_calibration；栈深 ≤24、H·W ≤4096×4096，超限 ResourceScaleMismatch 先拒绝
- **`sar.vh_ratio`** SAR VV/VH 极化比（`native`·成熟度 已验证）
  - 假设：仅线性功率/强度域比值 vv/vh（dB 对数域输入 → UnsupportedMethod类型化拒绝——负值守卫 + 显式量纲声明）；dB 域对比请改用 log-ratio（sar.log_ratio_change，VV−VH 语义）；VH=0 → NaN；同景双极化（如 Sentinel-1 VV+VH）
  - 局限：无辐射定标假定下仅作结构对比代理，非物理量
- **`sar.log_ratio_change`** SAR 双时相对数比值变化（`native`·成熟度 已验证）
  - 假设：log(a)−log(b)：对数域对称（增强=衰减镜像），SAR 双期惯用量；经 detect_ratio_change 工具 method=log_ratio 参数执行
  - 局限：比值不区分变化原因；输入须为正（线性强度或 dB）

## `sar_coherence` — SAR 相干性估计

复数 SLC 双通道相干性 γ 窗口估计（|Σ a·b*|/√(Σ|a|²Σ|b|²)；EXPERIMENTAL——无轨道元数据/配准质量披露；强度-only 输入类型化拒绝）。

- **`sar.coherence`** 复数相干性估计（窗口化）（`native`·成熟度 实验，契约: `sar_coherence_analysis`，出处: `oliver_quegan1998`）
  - 假设：γ = |Σ a·b*| / √(Σ|a|²·Σ|b|²)（窗口化，nodata 感知累加）；输入为双通道复 SLC（(re, im) 二元组或 complex）——两历元同网格；分母为 0 的窗口 → NaN；γ 钳 [0,1]（超 1 像元计数披露）
  - 局限：EXPERIMENTAL：无轨道元数据/配准质量输入——窗口估计有偏差，需人工核验；强度-only 输入（纯实数/虚部全零）被类型化拒绝（相位不可虚构）；不输出干涉相位/解缠（仅相干性幅度）

## `sar_radiometric_calibration` — SAR 辐射定标

DN → β⁰/σ⁰/γ⁰ 常数辐射定标（定标常数显式必需；逐像元 LUT 与热噪声去除未实现——披露）。

- **`sar.radiometric_calibration`** SAR 辐射定标（β⁰/σ⁰/γ⁰；标量 K + 入射角 LUT）（`native`·成熟度 已验证，契约: `sar_calibration_analysis`，出处: `oliver_quegan1998`）
  - 假设：标准定标关系：β⁰=I/K、σ⁰=β⁰·sin(θᵢ)、γ⁰=β⁰·tan(θᵢ)，I=DN²（振幅域）；calibration_constant（K，如 Sentinel-1 A²/AUT）显式必需——缺失拒绝；入射角：标量或逐像元 2D LUT（与网格同形，(0,90) 开区间；lut_pixels 披露；V3 additive）
  - 局限：定标常数 K 为标量——σ⁰ 逐像元定标 LUT（SAFE annotation XML）不解析（入射角 LUT 已支持）；热噪声去除为独立算法 sar.thermal_noise_removal（本工具不做隐式前置/后置）；不修正地形起伏（地形辐射校正见独立算法 sar.rtc）
- **`sar.log_scaling`** SAR 量纲换算（振幅/强度/dB 恒等式）（`native`·成熟度 已验证，契约: `sar_log_scaling_analysis`）
  - 假设：纯代数恒等式：I=A²、A=√I、dB=10·log₁₀(x)、x=10^(dB/10)；round-trip 精确（float64 恒等；测试锁定）；线性→dB 对 ≤0 钳 ε=1e-12 下限（计数披露，非静默）
  - 局限：无定标语义（量纲假定由调用方负责）——只做换算；振幅/强度域负值物理无意义 → NaN（计数披露）
- **`sar.thermal_noise_removal`** SAR 热噪声去除（噪声底/LUT 相减）（`native`·成熟度 已验证，契约: `sar_thermal_noise_removal_analysis`，出处: `oliver_quegan1998`, `esa_s1_ipf_denoising`）
  - 假设：I_dn = max(I − N, 0)：噪声项 N 为标量噪声底或同形逐像元 LUT（互斥）；输入须线性强度（非负；dB 输入被拒绝）；input_domain（可选）=auto/linear/db 显式声明量纲：负值检测为符号启发式（全正 dB 场不可检测），显式 db → 类型化拒绝；缺省 auto 行为不变
  - 局限：不解析 Sentinel-1 SAFE annotation XML（denoising 需逐 swath 插值；ESA S-1 MPC 技术注记 MPC-0392 / ESA-RS-CLI-52-0946）——仅接收已提取的噪声底/LUT；钳 0 使弱信号像元强度统计右偏（正偏披露，不静默）

## `sar_speckle_filtering` — SAR 斑点滤波

SAR 相干斑点噪声抑制（Lee 1980 / Refined-Lee 边缘方向 MMSE / Frost 1982；ENL 显式优先、缺省矩估计披露；refined_lee 为 7 子窗近似实现）。

- **`sar.multitemporal_speckle`** 多时相斑点抑制（强度域 MT-Lee）（`native`·成熟度 已验证，契约: `sar_multitemporal_speckle_analysis`，出处: `lee1980`, `oliver_quegan1998`）
  - 假设：逐切片：时序均值与空域 Lee 估计的逐像元逆方差加权（确定性）；权重 σ²：空域=Lee 残差代理 k²·Var；时序=Var_temp/n_t（n_t<2 回退空域）；栈已配准对齐；ENL 显式优先（缺省整图矩估计，披露）
  - 局限：非 Quegan 谱域多时相滤波（需 SLC 复数相干分解）——强度栈近似，披露；时序方差计入真实地物变化 → 权重保守偏向空域估计；栈深 ≥3 且 ≤24、H·W ≤4096²（超限 ResourceScaleMismatch）
- **`sar.speckle_filter`** SAR 斑点噪声滤波（Lee/Refined-Lee/Frost/Gamma MAP/Kuan）（`native`·成熟度 已验证，契约: `sar_speckle_filter_analysis`，出处: `lee1980`, `lee1981`, `lopes1990`, `frost1982`, `kuan1985`, `lee_jurkevich1994`）
  - 假设：斑点为乘性噪声（x=R·n）；输入须线性强度（非负，dB 被拒绝）；ENL 显式参数优先；缺省整图矩估计 ENL=mean²/var（均匀假设，披露）；窗口 ∈ {3,5,7}；窗口统计 nodata 感知（全无效窗口 → NaN）
  - 局限：refined_lee 子窗选择为 MSE 代理（方差+中心偏差²）——非 Lopes 1990 完整 MAP 变体；gamma_map 为 Lopes 1990 / Lee & Jurkevich 1994 滤波核实现——不含完整先验结构比模型；发散像元冻结上一迭代（确定性披露）；斑点抑制同时平滑真实纹理；不恢复被斑点淹没的像元信息
- **`sar.enl_map`** 滑窗 ENL 估计图（`native`·成熟度 已验证，契约: `sar_enl_map_analysis`，出处: `oliver_quegan1998`）
  - 假设：ENL = mean²/var（滑窗、总体方差 ddof=0、nan 感知）；全局 ENL 由整图有效像元估计（均匀假设）；enl_ci95：全局 ENL 的 Wald 95% CI（delta 法 var(ENL̂)≈2·ENL·(ENL+1)/n，均匀场景；下界钳 0）
  - 局限：非均匀窗口把纹理方差计入 → ENL 被低估（估计偏差，披露）；CI 只覆盖抽样噪声、不覆盖非均匀偏差（均匀场景假设披露）；dB 输入被拒绝（矩估计仅线性强度有意义）

## `sar_terrain_geometry_correction` — SAR 地形几何/辐射校正

SAR 地形效应校正：RTC gamma 平坦化（Small 2011，γ_flat=σ⁰·cosθi/cosθl）与叠掩/阴影几何分类（{0=normal,1=layover,2=shadow,3=nodata}；Horn 坡度坡向 + 本地入射角；range-only 无轨道元数据简化披露）。

- **`sar.layover_shadow`** SAR 叠掩/阴影几何分类（`native`·成熟度 已验证，契约: `sar_layover_shadow_analysis`，出处: `small2011`, `horn1981`）
  - 假设：分类 {0=normal,1=layover,2=shadow,3=nodata} + 占比；layover = 面坡（cos(β−β_r)>0）且坡度陡于入射角（α > θi）；shadow = 本地入射角余弦 ≤ 0（背坡超掠射角）
  - 局限：cell_size 必须为米制单位：度网格 DEM 的像元尺寸需调用方先换算（地形域的 cos(lat) 自动换算不在本域内）；range-only 几何简化：无轨道元数据/传感器位置（传感器位置无关近似，披露）；不含视线遮蔽（ray-casting cast shadow）——单像元几何判定
- **`sar.rtc`** SAR 地形辐射校正 RTC（gamma 平坦化）（`native`·成熟度 已验证，契约: `sar_rtc_analysis`，出处: `small2011`, `horn1981`）
  - 假设：γ_flat = σ⁰·cosθi/cosθl（Small 2011 gamma 平坦化）；本地入射角：cos θl = cosθi·cosα + sinθi·sinα·cos(β−β_r)（α=坡度、β=下坡方位角、β_r=雷达视线方位角）；DEM Horn 3×3 梯度；北朝上网格；方位角顺时针自北
  - 局限：cell_size 必须为米制单位：度网格 DEM 的像元尺寸需调用方先换算（地形域的 cos(lat) 自动换算不在本域内）；range-only 几何简化：无轨道元数据/传感器位置/方位向分量（披露）；叠掩（面坡且 α>θi）与阴影（cosθl≤0）→ nodata（计数披露）

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

## `space_time_k_function` — 时空 K 函数

时空 K 函数 K_st(r,t)（Diggle 1995）——二阶时空聚集强度随空间/时间尺度的谱（与 Knox 单一阈值检验互补），时间置换包络。

- **`point_pattern.space_time_k`** 时空 K 函数 K_st(r,t)（`native`·成熟度 已验证，契约: `space_time_k_analysis`，出处: `diggle1995`, `ripley1976`）
  - 假设：K_st(r,t)=|W|·T/(n(n−1))·Σ_{i≠j} I(d≤r)I(|Δt|≤t)/w_ij（有序对双向计入；w_ij 与单变量 K 同款各向同性校正）；独立零假设参考 K_st=πr²·2t（K_s=πr² 与 K_t=2t 之积）；显著性：时间标签置换（固定种子 42），sup(K_st−ref) 单侧 greater
  - 局限：时间维无边缘校正：观测窗端点附近 Δt 分布被截断，结论对窗长敏感（meta 中 temporal_edge_note 披露）；O(n²) 成对统计：空间对稀疏化 + 配对预算先估后分配，上限 2 万点；p 值分辨率 1/(permutations+1)，上限 499

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
  - 回退：`interpolation.kriging`→approximation
  - 资源包络：24B/要素，像元硬上限 1500000
  - 取消：coarse
  - 数值容差：rtol=1e-06，atol=1e-09
- **`interpolation.nearest_neighbor`** 最近邻插值（`native`·成熟度 已验证，契约: `nearest_neighbor_analysis`，出处: `thiessen1911`，精度: exact）
  - 假设：每个格点取最近样本值（cKDTree k=1）：输出为样本的 Voronoi（泰森）分段常值场；无平滑：表面在单元边界处不连续（跳变是方法语义，非缺陷）；全域有值：凸包外为最近样本外推（已披露，无不确定性声明）
  - 局限：无理论方差，无残差验证证据（跳变场 LOOCV 无意义）；>20 万样本 / >400 万目标格点类型化拒绝（先拒绝不 OOM）；需要连续平滑表面时改用 IDW / kriging / 自然邻域
  - 回退：`interpolation.idw`→approximation
  - 资源包络：8B/像元，要素硬上限 200000，像元硬上限 4000000
  - 取消：coarse
  - 数值容差：rtol=1e-12，atol=0
- **`interpolation.rbf`** RBF 径向基插值（`native`·成熟度 已验证，契约: `rbf_interpolation`，出处: `duchon1977`，精度: exact）
  - 假设：scipy RBFInterpolator：核薄板样条默认，smoothing=0 时精确过样本点；米制距离：地理输入经 estimate_utm_crs 自动投影（与 IDW 同一 CRS 政策）；局部 RBF（neighbors ≤64）：超样本数时按 KdTree 最近邻截断
  - 局限：多二次/高斯类核在大数据集上病态（本实现未含 gaussian 核）；>2 万点确定性行距抽稀（metadata.disclosures 披露），>10 万点拒绝；外推区域行为由核多项式项主导，远端可能发散（无钳制）
  - 回退：`interpolation.idw`→approximation
  - 资源包络：8B/要素，要素硬上限 100000
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`interpolation.kriging`** 普通克里金插值（`native`·成熟度 生产，契约: `kriging_interpolation`，出处: `matheron1963`，精度: exact）
  - 假设：二阶平稳性假设：变异函数从数据估计（加权 RSS 最低的模型胜出）；规范半方差构造（Isaaks & Srivastava）：nugget 进所有 h>0 项与 γ₀，对角为零；k 邻域（≤24）系统分批求解；高斯模型加 ridge 稳定化，退化逐格计数
  - 局限：EPSG:3857 被接受为工作 CRS 但含 Web Mercator 尺度畸变（高纬非真实地面距离）；趋势明显的场 OK 有系统偏差——改用 interpolation.universal_kriging；变异函数拟合失败 / 滞后 bin 不足时结构化拒绝（不静默降级）
  - 回退：`interpolation.idw`→approximation
  - 资源包络：24B/要素，对预算 200000，要素硬上限 500000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-06，atol=1e-09
- **`interpolation.universal_kriging`** 泛克里金插值（`native`·成熟度 已验证，契约: `kriging_interpolation`，出处: `matheron1963`，精度: exact）
  - 假设：线性漂移 E[Z(x)]=b0+b1·x+b2·y；变异函数在 OLS 去趋势残差上拟合；UK 系统带趋势约束 Lagrange 乘子；方差 = wᵗγ₀ + mᵗf0；零残差退化（数据严格线性）→ 精确趋势预测、方差 0、披露 zero_residual_variance
  - 局限：漂移阶数固定为线性（二次及以上趋势未实现）；EPSG:3857 被接受为工作 CRS 但含 Web Mercator 尺度畸变（与 OK 同）；样本 <12 拒绝（InsufficientSamples）；普通克里金 ≥8 即可
  - 回退：`interpolation.kriging`→approximation
  - 资源包络：24B/要素，对预算 200000，要素硬上限 500000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-06，atol=1e-09
- **`interpolation.simple_kriging`** 简单克里金（`native`·成熟度 已验证，契约: `kriging_interpolation`，出处: `matheron1963`，精度: exact）
  - 假设：SK 协方差形式 C(h)=(nugget+sill)−γ(h)：pred=m+wᵗ(z−m)，var=C(0)−wᵗc₀；先验均值是模型输入：mean 参数缺省时以样本均值估计并在 disclosures 披露；nugget>0 时 SK 不是精确插值器（C(0)≠C(0⁺)，理论语义，非数值缺陷）
  - 局限：先验均值的可信度决定 SK 的优势——均值未知且样本均值有偏时改用 OK；EPSG:3857 工作 CRS 的 Web Mercator 尺度畸变（与 OK 同）；样本 <8 拒绝（与 OK 同底）
  - 回退：`interpolation.kriging`→approximation
  - 资源包络：24B/要素，对预算 200000，要素硬上限 500000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-06，atol=1e-09
- **`interpolation.external_drift_kriging`** 外部漂移克里金（`native`·成熟度 已验证，契约: `kriging_interpolation`，出处: `matheron1963`，精度: exact）
  - 假设：KED：漂移场 d(x) 在样本与目标处都已知；系统带 [1, d] 两个约束乘子；驱动层目标处漂移经 IDW(k=5,power=2) 近似——approximate 分量已披露；漂移场常量（零方差）结构化拒绝（外部漂移不可识别）
  - 局限：目标处漂移的 IDW 近似误差进入趋势项（与 regression_kriging 同款近似语义）；EPSG:3857 工作 CRS 的 Web Mercator 尺度畸变（与 OK 同）；CV 不支持（诚实省略，不伪造 CV 指标）
  - 回退：`interpolation.universal_kriging`→approximation
  - 资源包络：32B/要素，对预算 200000，要素硬上限 500000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-06，atol=1e-09

## `spatial_join` — 空间连接

按拓扑关系把右表属性挂到左表（区别于几何裁剪）。

- **`geometry.spatial_join`** 空间连接（`native`·成熟度 —）
  - 假设：谓词连接（intersects/within/contains），左表输出
  - 局限：大表走空间索引（STRtree）；连接谓词语义见工具描述；不量度（工作帧非投影）——面积/长度属性不在此层生成

## `spatial_regression` — 空间回归

OLS+空间诊断 / SLX / SAR-ML / SEM-ML（LM 决策树支撑）。

- **`spatial.ols_regression`** OLS + 空间诊断（`native`·成熟度 已验证，契约: `ols_regression_analysis`，出处: `anselin1988`, `jarque_bera1980`, `breusch_pagan1979`, `moran1950`, `mackinnon_white1985`）
  - 假设：y~X（含截距）；lstsq 求解，se/t/p 由 (X'X)⁻¹σ² 给出；cov_type=classic（默认）行为与历史逐位一致；HC0/HC1/HC3 附 MacKinnon-White 异方差稳健标准误列（系数不变）；残差 Moran's I 固定种子 42 置换（双侧 +1）
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

## `spatial_weights_diagnostics` — 空间权重诊断

权重结构体检：稀疏度/对称性/邻居分布/孤岛/连通分量。

- **`stats.weights_diagnostics`** 空间权重诊断（`native`·成熟度 已验证，契约: `weights_diagnostics_analysis`，出处: `anselin1988`）
  - 假设：诊断对象=既有空间权重构造器（knn/queen/rook/distance_band）产物；对称性分别检查存储矩阵与二值邻接（行标准化矩阵一般不对称）；连通分量在二值邻接的无向图上计算（networkx）
  - 局限：诊断只覆盖权重结构，不覆盖权重方案的选择恰当性；连通分量是无向近似：有向 kNN 的互邻关系按无向边处理

## `spatiotemporal_clustering` — 时空聚类

ST-DBSCAN 等时空聚类（与 LISA 局部自相关是不同检验）。

- **`temporal.hotspot`** 时空热点簇（ST-DBSCAN）（`native`·成熟度 已验证，出处: `ester_kriegel1996`）
  - 假设：ST-DBSCAN 时空密度聚类：eps_spatial_m（米）/ eps_temporal_days（天）/ min_samples 参数语义；输出为时空簇计数与成员要素（描述性密度聚类）；不是逐期 Gi*、不做 Emerging Hotspot 演化分类（后者见 temporal.emerging_hotspot）；时间字段解析 NaT 剔除并披露（与 temporal.profile 同约定）
  - 局限：无自动带宽：eps 需调用方给定，结果对 eps/min_samples 敏感（参数披露）；簇计数输出无显著性检验语义（密度聚类的诚实边界）
- **`stats.st_dbscan`** 时空 DBSCAN 聚类（`native`·成熟度 已验证，出处: `ester_kriegel1996`）
  - 假设：ST-DBSCAN：空间 ε（米，自动投影 UTM）+ 时间 ετ 双阈值；时间字段解析 NaT 剔除并披露
  - 局限：minPts/ε 选择敏感（无自动带宽）；簇数为结果而非假设

## `spatiotemporal_interpolation` — 时空插值

(x,y,t) 时空协方差克里金（separable/product-sum，秒制时间）；目标时刻表面 + 方差。

- **`interpolation.st_kriging`** 时空克里金（`native`·成熟度 已验证，契约: `st_kriging_analysis`，出处: `goovaerts1997`, `cressie1999`，精度: exact）
  - 假设：时间单位秒（epoch/相对秒由调用方声明）；空间米制（自动投影）；product_sum：双时间尺度可分离混合 s·ρ_s·[w·ρ_t(τ/r)+(1−w)·ρ_t(τ/3r)]——正组合按构造半正定（De Iaco product-sum 类）；separable：C=s·ρ_s·ρ_t 严格有效；τ=0 两模型都精确退化为空间协方差；邻域 = 空间 k 近邻 × 时间窗过滤；窗内不足时放宽为纯空间 k 近邻（计数披露）
  - 局限：时间相关为单参数指数形状（非参数时间变异函数未实现）；时空交叉结构不可识别时 product-sum 退化为可分离的加权和（已披露）；EPSG:3857 工作 CRS 的 Web Mercator 尺度畸变（与 OK 同）
  - 回退：`interpolation.kriging`→approximation
  - 资源包络：40B/要素，要素硬上限 300000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-09，atol=0

## `spectral_index` — 类型化光谱指数

按语义角色（red/nir/swir1/...）显式命名的 12 公式族光谱指数（含出处与值域诚实报告）。

- **`remote.spectral_index`** 类型化光谱指数（13 公式族）（`native`·成熟度 已验证，契约: `spectral_index_analysis`，出处: `rouse1974`, `huete1988`, `gao1996`, `xu2006`, `zha_woodcock2003`, `key_benson2006`, `mcfeeters1996`）
  - 假设：波段按语义角色显式命名（band_map），绝不按波段位置猜测；线性定标先于公式（DN/10000→反射率）；零分母→NaN；超理论值域只报告不钳制（out_of_range_fraction）
  - 局限：公式出处逐指数声明（gndvi/msavi/ndmi 无词表出处，诚实留空）；EVI/EVI2 常数项只在反射率单位下成立（#382）

## `spectral_target_detection` — 光谱目标检测

已知光谱签名下的逐像元目标检测/相似度：光谱角 SAM（Kruse 1993）、光谱信息散度 SID（Chang 2000）、匹配滤波（Boardman 1995）。

- **`remote.matched_filter`** 匹配滤波目标检测（`native`·成熟度 已验证，契约: `matched_filter_analysis`，出处: `boardman1995`）
  - 假设：score = tᵀΣ⁻¹(x−μ)/(tᵀΣ⁻¹t)；μ/Σ 由全场景公共有效像元估计；纯目标像元得分≈1、背景≈0（丰度式解读）；目标向量与波段序逐波段对齐（band_order 披露）
  - 局限：单高斯背景假设——强背景结构会污染白化统计；零方差波段剔除（dropped_bands 披露）；pinv 伪逆数值稳定
- **`remote.sam`** 光谱角制图（SAM）（`native`·成熟度 已验证，契约: `sam_analysis`，出处: `kruse1993`）
  - 假设：θ=arccos(⟨x,e⟩/(‖x‖·‖e‖))（弧度缺省，degrees 可选）；端元向量与波段序逐波段对齐（band_order 披露，不按位置猜测）；零范数像元（无亮度）→ NaN；零范数端元全 NaN 并披露
  - 局限：只度量光谱形状（对亮度增益不变），不区分亮度差异；argmin 类别仅在有有限角度的端元上取（全 NaN → NaN）
- **`remote.sid`** 光谱信息散度（SID）（`native`·成熟度 已验证，契约: `sid_analysis`，出处: `chang2000`）
  - 假设：对称形式 D(x,e)=Σp·ln(p/q)+Σq·ln(q/p)，p=x/Σx、q=e/Σe；像元/端元出现非正分量或非正和 → NaN（熵在非正测度无定义）；要求反射率类正值输入（SAR dB 等不适用，披露）
  - 局限：非正分量占比仅报告（nonpositive_fraction），不做钳制；SID 比 SAM 对分布差异更敏感，但对定标噪声同样敏感

## `spectral_unmixing` — 线性光谱解混

FCLS 全约束最小二乘线性光谱解混（Heinz & Chang 2001；逐像元 min‖Ex−f‖² s.t. x≥0, Σx=1；丰度面 [0,1] + RMS 残差面；端元由调用方提供，列满秩守卫）。

- **`remote.linear_unmixing`** 线性光谱解混（FCLS 全约束最小二乘）（`native`·成熟度 已验证，契约: `linear_unmixing_analysis`，出处: `heinz_chang2001`）
  - 假设：线性混合模型 f = E·x + ε；逐像元 min‖Ex−f‖² s.t. x≥0, Σx=1；端元矩阵 E（k 波段 × m 端元）逐波段对齐且列满秩（秩亏拒绝）；单纯形内部像元走和一约束闭式解（精确）；负分量像元走 δ-增广 NNLS（δ=1e6 归一尺度，和一违背 ~O(1/δ)，计数披露）
  - 局限：仅线性混合模型——非线性混合（intimate mixing/多层散射）不适用；端元由调用方提供（可接 remote.endmember_vca 输出）；端元质量决定丰度质量，本算法不校验端元的物理合理性；n_bands·H·W ≤ 16M 像元总量（无流式实现）；欠定 m>k 被秩亏守卫拒绝

## `tasseled_cap_transformation` — Tasseled Cap 冠层变换

传感器系数注册表驱动的亮度/绿度/湿度三轴变换（landsat5_tm=crist_cicone1984、landsat8_oli=baig2014、sentinel2=shi_xu2019；六语义角色显式映射）。

- **`remote.tasseled_cap`** Tasseled Cap 冠层变换（传感器系数注册表）（`native`·成熟度 已验证，契约: `tasseled_cap_analysis`，出处: `crist_cicone1984`, `baig2014`, `shi_xu2019`）
  - 假设：系数行按传感器显式注册（landsat5_tm/landsat8_oli/sentinel2）；波段按六语义角色显式映射（blue/green/red/nir/swir1/swir2）；reflectance_domain 仅披露（baig2014/shi_xu2019 于 at-satellite 推导）
  - 局限：线性变换不改信息总量（3 轴是 6 波段旋转投影，非独立观测）；未注册传感器显式拒绝（不默认套用他传感器系数）

## `temporal_aggregate` — 时间聚合

按时间窗重采样汇总。

- **`temporal.aggregate`** 时间聚合（`native`·成熟度 已验证）
  - 假设：按时间粒度分组聚合（描述性）；NaT 剔除并披露；count=分组计数；sum/mean/min/max 作用于显式 metric_fields
  - 局限：分组键时区语义不归一（诚实披露）；空分组/全 NaT 不伪造统计（类型化空结果）

## `temporal_change_point` — 时序均值变点

CUSUM 单均值漂移定位 + 固定种子 bootstrap 显著性（多变点不在模型内）。

- **`temporal.changepoint`** CUSUM 均值变点（`native`·成熟度 已验证，契约: `temporal_changepoint_analysis`，出处: `page1954`）
  - 假设：单均值漂移假设：变点 = argmax|Σ(x−x̄)|（k 取 1..n−1）；显著性 = 无变化零假设下固定种子 bootstrap 的 max-CUSUM 分布；p ≥ alpha 时不给 change_point_index（candidate 恒给）
  - 局限：多变点/方差变化不在模型内；n<10 变点定位不稳定（警告）；bootstrap p 分辨率 1/(draws+1)

## `temporal_composite` — 多时相合成

多时相波段栈合成（medoid 多维中位数——Flood 2013；选真实观测切片保持跨波段光谱一致性；NaN 切片整条剔除；generic 时序统计，光学/SAR 栈通用）。

- **`remote.medoid_composite`** medoid 时序合成（多维中位数）（`native`·成熟度 已验证，出处: `flood2013`）
  - 假设：逐像元选 argmin_t Σ_s ‖x_t−x_s‖₂（波段欧氏）的**真实观测切片**——跨波段光谱一致性保持（区别于逐波段 median 的独立分位拼接）；任一波段无效（NaN/哨兵）的切片整条剔除（跨波段一致性优先，不做波段级稀释）；平局取最早时相（确定性）；实现为 generic 时序统计（与 sar.temporal_composite 同底座家族，光学/多时相栈通用）；云/影污染时相经距离和自动边缘化
  - 局限：输入须已配准对齐的多时相波段栈 (T,k,H,W)；不做云检测/掩膜；规模预算：2≤T≤24、H·W≤4096²、T·H·W≤32M（距离累加面，超限拒绝）；k=1 时退化为最接近全体一维观测的选择（奇数 T 下与 median 等价）

## `temporal_feature_extraction` — 时序特征提取

逐像元时序特征（min/max/mean/std/amplitude/first−last + 单周期谐波；无物候模型拟合——线性趋势 + 单谐波边界披露）。

- **`remote.temporal_features`** 时序特征提取（`native`·成熟度 已验证，契约: `temporal_features_analysis`）
  - 假设：栈第 0 轴 = 时间序；std 为总体标准差（ddof=0，披露）；谐波 = [1, t, sin(2πt), cos(2πt)] 联合 LS（单周期 = 栈跨度）；谐波要求完整序列 + 满秩设计（T≥4），否则 NaN（披露）
  - 局限：无物候模型拟合（无双谐波/SG 滤波/物候期提取）——诚实边界；first−last 对首尾无效像元 → NaN；min/max/mean 对有限切片 nan-aware

## `temporal_profile` — 时间画像

时间字段/跨度/粒度画像（元数据，不产新数据）。

- **`temporal.profile`** 时间画像（`native`·成熟度 已验证）
  - 假设：时间字段解析 NaT 剔除并披露（与 ST-DBSCAN 同约定）；画像/聚合为描述性统计，不做趋势推断
  - 局限：无时区归一（时间戳语义由输入披露决定）；空时间维度 → 类型化错误（不伪造空统计）

## `temporal_trend` — 时序趋势

时间维度的趋势/聚合/时空热点分析。

- **`temporal.trend`** 时序趋势（`native`·成熟度 已验证，契约: `temporal_trend_analysis`，出处: `sen1968`, `mann1945`, `kendall1975`）
  - 假设：缺省 ols_sen：Sen 中位斜率 + OLS，行为与历史逐位一致；MK 族：tie 校正方差 + 连续性校正正态 z + 双侧 p；显著性证据仅在 mann_kendall/seasonal 分支产出（ols_sen 无 p 值）
  - 局限：序列相关（lag-1 秩自相关超限）会夸大 MK 显著性——结果内警告；季节 MK 无预白化（prewhitening 未实现）；观测 <3 的季节跳过并披露；两时间点无法定义趋势统计量（n=2 拒绝，非降级描述）
- **`temporal.seasonal_decompose`** 经典季节分解（`native`·成熟度 已验证，契约: `seasonal_decompose_analysis`，出处: `makridakis1998`）
  - 假设：经典 MA 分解：趋势=奇数窗口（period）中心滑动平均；季节指数=去趋势值按相位 t mod period 的组均值；additive 归一化 Σs=0；余项 additive = y−trend−seasonal；multiplicative = y/(trend·seasonal)
  - 局限：经典 MA 分解不是 STL——无迭代稳健拟合、无季节平滑，对离群值敏感；period 必须为奇数（偶数窗口的中心 MA 需 2×m 复合平均，显式拒绝）；multiplicative 要求序列严格为正
- **`temporal.raster_ts`** 时序栅格（`native`·成熟度 已验证）
  - 假设：时序栅格切片统计（逐期描述性统计）
  - 局限：栈深与格网规模守卫在实现层（ResourceScaleMismatch）

## `terrain_aspect` — 坡向分析

DEM 坡向。

- **`terrain.aspect`** 坡向（`native`·成熟度 已验证，出处: `horn1981`）
  - 假设：3×3 Horn 梯度；度栅格需 z_factor/纬度修正；坡向 = 下坡方位（顺时针自北 0-360°）；平地 → NaN
  - 局限：平地/近平地坡向数值不稳定（梯度趋于 0）；边界像元 edge 复制延拓（单侧差分）
  - 资源包络：40B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09

## `terrain_contours` — 等值线提取

DEM 等值线提取（marching squares → GeoJSON LineString，顶点映射到世界坐标；nodata 断线）。

- **`terrain.contours`** 等值线提取（`native`·成熟度 已验证，契约: `extract_contours`）
  - 假设：marching squares 等值线（matplotlib Agg，无显示环境）；水平选取优先级：显式 levels > interval（自 vmin 等间隔）> n_levels（vmin..vmax 等间隔）；nodata/非有限像元 → NaN 断线；顶点经栅格仿射变换映射到世界坐标
  - 局限：level == 数据极值的退化等值线可能为空（不产要素，meta 披露 levels_drawn）；顶点密度受像元网格限制（无样条平滑/加密）
  - 资源包络：16B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09

## `terrain_derivatives` — 地形衍生指标

DEM 邻域地形指标：TPI（Weiss 2001）/TRI（Riley 1999）/粗糙度（Wilson 2007）与平面、剖面曲率（Zevenbergen-Thorne 1987）。

- **`terrain.tpi`** 地形位置指数 TPI（`native`·成熟度 已验证，契约: `terrain_derivative`，出处: `weiss2001`）
  - 假设：TPI = z − 窗口均值（含中心像元）；线性坡面上 ≡ 0；窗口为 3-101 奇数；边界收缩为可得像元（不发明填充值）；与像元尺寸无关（高程同量纲输出）
  - 局限：Weiss 地类分级需双尺度（如 3/25 格）对照，单一窗口不构成分类；积分图均值-平方差在窗口均值远大于离散度时有浮点精度损失
  - 资源包络：32B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.tri`** 地形崎岖度指数 TRI（`native`·成熟度 已验证，契约: `terrain_derivative`，出处: `wilson2007`）
  - 假设：TRI = sqrt(Σ(z − z_nb)²)，8 个直接邻域（Riley 1999 原式）；边界收缩为可得邻域；平坦面 ≡ 0
  - 局限：只反映 1 像元尺度起伏，不表征多尺度崎岖度；各向异性像元不做距离加权（与 Riley 原式一致的纯差分）
  - 资源包络：32B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.roughness`** 地形粗糙度（`native`·成熟度 已验证，契约: `terrain_derivative`，出处: `wilson2007`）
  - 假设：粗糙度 = 窗口内高程总体标准差（ddof=0）——注意：Wilson (2007) 原文粗糙度为 max−min 口径，本实现采用窗口 std 惯用口径（与引用差异如实披露）；窗口为 3-101 奇数；边界收缩为可得像元
  - 局限：对离群高程敏感（无稳健尺度）；积分图方差在窗口均值远大于离散度时有浮点精度损失
  - 资源包络：32B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.curvature`** 平面/剖面曲率（`native`·成熟度 已验证，契约: `terrain_derivative`，出处: `zevenbergen_thorne1987`）
  - 假设：Zevenbergen-Thorne 二阶差分：profile 沿最陡下降方向、plan 沿等高线方向；单位 z_units·cell⁻²（惯例 ×100 报告；元数据披露）；符号约定：profile>0 凸（水流减速）/ plan>0 分散；z=x² 检验 profile=+2、plan=0
  - 局限：3×3 模板对噪声敏感（无预平滑）；边界像元 edge 复制延拓退化为单侧差分
  - 资源包络：48B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.solar_radiation`** 晴空太阳辐射（`native`·成熟度 已验证，契约: `hydrology_v4_analysis`，出处: `fao56`，精度: heuristic）
  - 假设：FAO-56 大气顶日辐射 Ra（dr/δ/ωs 解析）× 晴空透射（0.75）；地形入射因子 f = cos β + sin β·cos(az_sun − aspect)，钳 ≥0.15（散射底）；日积分代表方位 az_sun = π + δ（单方位近似）
  - 局限：heuristic：无地平线遮蔽积分/多时步太阳轨迹（horizon_angle 工具可做后处理）；海拔-大气修正未含（Rso 常数透射）
  - 资源包络：48B/像元
  - 取消：coarse
  - 数值容差：rtol=0.02，atol=0.5

## `terrain_geomorphometry` — 地貌形态分类

地形开放度（Yokoyama 2002）、geomorphons 地貌分类（Jasiewicz & Stepinski 2013）、Weiss 双尺度 TPI 地类分级与多方位山体阴影。

- **`terrain.hypsometry`** 高程面积分析（`native`·成熟度 已验证，契约: `hydrology_v4_analysis`，出处: `strahler1952`，精度: approximate）
  - 假设：曲线 a(e) = 高于归一化高程 e 的面积占比（n_levels 级直方）；HI = ∫a de（矩形 = 1；Strahler 1952 侵蚀循环代理）
  - 局限：直方分级离散化（连续曲线的级别近似）；常数面退化 HI=0（诚实披露，不伪造曲线）
  - 资源包络：8B/像元
  - 取消：coarse
  - 数值容差：rtol=1e-09，atol=0
- **`terrain.openness`** 地形开放度（`native`·成熟度 已验证，契约: `openness_analysis`，出处: `yokoyama2002`）
  - 假设：正开放度 = mean_φ max_d arctan((z₀−z(d))/d)；负开放度同式取反向差（度）；16 方位（4-64 可调）× 半径 1..R 像元；偏移圆整后的实际米制距离；平地 ≡ 0；山脊高正开放度、谷地高负开放度幅值
  - 局限：方位离散 ≤ 360/azimuth_count（默认 22.5°）角分辨率；无有效采样的方位从均值剔除（栅格角隅诚实退化）；半径 ≤ 100 像元护栏（射线行走内存/时间包络）
  - 资源包络：32B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.geomorphons`** Geomorphons 地貌分类（`native`·成熟度 已验证，契约: `geomorphon_analysis`，出处: `jasiewicz_stepinski2013`）
  - 假设：8 方位视线三元码（zenith/nadir 角 vs flatten 容差）→ 10 类决策表；决策表（优先级级联）：全-1 summit；全+1 depression；≥6 环 ridge/valley；双 3-5 环 slope；单 3-5 环 shoulder/hollow；1-2 环 spur/footslope；否则 flat；far>0 跳过近场采样（skip 半径）；无采样腿按 0（平）计并披露
  - 局限：相对高程形态学：无绝对坡度语义（缓坡大尺度可判 flat）；lookup ≤ 128 像元护栏；flatten=0 时 DEM 噪声直通分类；角隅像元方位被网格截断（无采样腿按平计）
  - 资源包络：24B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-12，atol=0
- **`terrain.landform`** 双尺度 TPI 地类分级（`native`·成熟度 已验证，契约: `landform_analysis`，出处: `weiss2001`）
  - 假设：Weiss 2001 双尺度标准化 TPI（TPI/SD）+ 高程百分位 10 类决策表；中性带 |TPI/SD|<1；平地带按 elevation_tolerance 截 percentile 分档；TPI 窗口含中心像元（与 terrain.tpi 同口径）；边缘收缩
  - 局限：窗口与容差需按景观尺度率定（缺省 3/25 格、0.1 为海报惯例起点）；常量面（TPI SD=0）→ DegenerateData（分类阈值无定义）
  - 资源包络：32B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-12，atol=0
- **`terrain.hillshade_multi`** 多方位山体阴影（`native`·成熟度 已验证，契约: `hillshade_multiazimuth`，出处: `horn1981`）
  - 假设：单方位公式与 band_math.compute_hillshade 逐位一致（#379 罗盘语义）；combine=mean 多方位均值（去阴影）/ min 逐像元最小（制图）；NaN 像元传播为 NaN（与渲染掩膜一致）
  - 局限：朗伯面近似：无次级散射/大气效应；3×3 Horn 梯度 edge 复制延拓（单侧差分）
  - 资源包络：56B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09

## `terrain_hillshade` — 山体阴影

DEM 山体阴影。

- **`terrain.hillshade`** 山体阴影（`native`·成熟度 已验证，出处: `horn1981`）
  - 假设：3×3 Horn 梯度；度栅格需 z_factor/纬度修正；罗盘方位光照模型：照度 = sin(alt)cos(θ) + cos(alt)sin(θ)cos(az − aspect)
  - 局限：无次级散射/大气效应（朗伯面近似）；边界像元 edge 复制延拓（单侧差分）
  - 资源包络：40B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09

## `terrain_hydrology` — D8 水文分析

D8 单向流流向（ESRI 2 的幂编码）、拓扑序汇流累积与逆 D8 上游流域圈定（平地/洼地为汇，不填洼）。

- **`terrain.flow`** D8 流向与汇流累积（`native`·成熟度 已验证，契约: `flow_analysis`，出处: `ocallaghan_mark1984`）
  - 假设：D8 单向流（ESRI 2 的幂编码 1=E…128=NE；0=sink/outlet）；最陡下降按米制像元距离（地理栅格 x 向 cos(lat)）；并列最陡取最低索引邻域；汇流累积 = 上游贡献像元数（不含自身；全流域出口 = N−1）
  - 局限：D8 单向流限制：格网平行流向偏差；多向流为独立算法 terrain.dinf_flow（Tarboton 1997，本包内已实现，不在本算法内混叠）；默认 flat_routing='none'：平地/洼地即汇（code 0）；可选 flat_routing='epsilon' 经 terrain.sink_fill 的 epsilon 填洼获得平地路由（meta 披露填充像元数与抬升量），默认路径保持不变；流出网格边界的流路终止（boundary = outlet，不外推）
  - 资源包络：48B/像元
  - 取消：none
  - 数值容差：rtol=1e-12，atol=0
- **`terrain.watershed`** 流域圈定（`native`·成熟度 已验证，出处: `ocallaghan_mark1984`）
  - 假设：逆 D8 BFS：汇入 pour point 的全部上游像元（含 pour point 自身）；依赖 D8 单向流语义（编码与平局裁决同 terrain.flow）
  - 局限：pour point 不做河道 snap（未对齐河道时流域偏小，由调用方负责）；D8 格网流向偏差会传递到流域边界
  - 资源包络：32B/像元
  - 取消：none
  - 数值容差：rtol=1e-12，atol=0
- **`terrain.breach`** 洼地切沟（Breaching）（`native`·成熟度 已验证，契约: `hydrology_v4_analysis`，出处: `lindsay2016`，精度: approximate）
  - 假设：Lindsay 2016 选择性切沟简化：填洼识别洼地 → epsilon 填面 D8 接收者链定位 pit→出口路径 → 沿路径下切（min 语义 = 最小开挖）；切沟线 = pit 高程 − k·epsilon（pit→出口方向严格下降）；只降不升：非洼地像元永不改高
  - 局限：路径为填面最陡下降链（非全局最小代价路径 LCP）；超深回退填洼（max_breach_depth 限制；计数披露）
  - 资源包络：32B/像元，像元硬上限 50000000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-09，atol=0
- **`terrain.hand`** 最近排水高程（HAND）（`native`·成熟度 已验证，契约: `hydrology_v4_analysis`，出处: `renno2008`，精度: exact）
  - 假设：HAND = z(cell) − z(D8 下游链第一个河网像元)；望远镜求和单遍；河网 = 填后 D8 汇流累积 ≥ threshold；边界排出且未遇河网 → NaN（诚实缺省，计数披露）
  - 局限：河网阈值敏感性：阈值决定『最近排水』的定义；洪泛区语义为地形近似（非水动力淹没模型）
  - 资源包络：40B/像元，像元硬上限 50000000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-12，atol=0
- **`terrain.pfafstetter`** Pfafstetter 编码（单级）（`native`·成熟度 已验证，契约: `hydrology_v4_analysis`，出处: `pfafstetter1989`，精度: exact）
  - 假设：干流 = 出口上溯每步取汇流最大的上游河网像元；偶数码 2,4,… 沿干流等分；4 大支流（junction 汇流降序）取奇数 1,3,5,7；支流子流域 = junction 上游河网像元（下游-first 归属）
  - 局限：单级层级（多级递归子盆地编码未实现——hierarchy_note 披露）；出口必须在河网上（否则类型化拒绝）
  - 资源包络：24B/像元，像元硬上限 50000000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-12，atol=0
- **`terrain.shreve`** Shreve 河流量级（`native`·成熟度 已验证，契约: `hydrology_v4_analysis`，出处: `shreve1966`，精度: exact）
  - 假设：量级 = 上游量级之和（源头 = 1）；拓扑序 = 降序高程；河网 = 汇流累积 ≥ threshold（与 streams/strahler 同口径）
  - 局限：单线程长河量级线性增长（对排水强度敏感、对形态不敏感——与 Strahler 互补）
  - 资源包络：24B/像元，像元硬上限 50000000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-12，atol=0

## `terrain_hydrology_advanced` — 高级地形水文

Priority-Flood 填洼（Barnes 2014，epsilon 单调变体）、D∞ 多向流（Tarboton 1997 比例分流）、流程长度、河网提取与 Strahler 分级、流域形态量测（Strahler 1957）。

- **`terrain.sink_fill`** Priority-Flood 填洼（`native`·成熟度 已验证，契约: `sink_fill`，出处: `barnes2014`）
  - 假设：Priority-Flood（Barnes 2014）heapq 漫水；种子 = 网格边界 + nodata 邻接有效像元；nodata/网格外视作排水出口；epsilon>0 时逐像元抬升 → 表面严格单调可排；meta 报告 filled_volume（z_units·m²）/filled_cell_count/max_fill_depth
  - 局限：epsilon=0 时填后平地仍为汇（与 d8 不发明路由语义衔接）；纯 Python 堆循环，>10M 像元耗时显著（护栏 50M 像元先拒绝）；无嵌套洼地深度分层报告（单层溢流面）
  - 资源包络：32B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.dinf_flow`** D∞ 多向流（`native`·成熟度 已验证，契约: `dinf_analysis`，出处: `ocallaghan_mark1984`）
  - 假设：8 三角面平面梯度最陡下降（Tarboton 1997）；角度弧度 ∈ [0,2π)，x=东 y=北；汇流按面内角度比例分流到两下游邻域；拓扑序（高程降序）累积；平地/洼地 → 角度 -1 哨兵；nodata → NaN；函数内不填洼
  - 局限：D∞ 不消解格网平行流向偏差的极端情形（面离散 45°）；推荐组合 fill_depressions(epsilon>0) 先行获得单调可排面；缺角邻域的面跳过（边缘只用可得邻域）
  - 资源包络：40B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.flow_length`** 流程长度（`native`·成熟度 已验证，契约: `flow_length_analysis`，出处: `ocallaghan_mark1984`, `strahler1957`）
  - 假设：downstream = 沿 D8 接收者到出口的米制步长和（汇/出口 = 0）；upstream = 距最远山脊源的最大路径长（MAX 口径，文档化）；步长 = hypot(Δcol·cx, Δrow·cy)；地理栅格由调用方传 cos(lat) 修正 cx
  - 局限：继承 D8 格网流向偏差（路径沿 8 邻域折线）；平地不路由（d8 code 0）→ 平地内长度为 0
  - 资源包络：32B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.streams`** 河网提取（`native`·成熟度 已验证，契约: `stream_network`，出处: `strahler1957`）
  - 假设：河网像元 = 汇流累积 ≥ threshold（上游贡献像元数口径）；阈值由调用方按流域尺度率定（无普适默认）
  - 局限：阈值敏感：过低生成伪河网、过高断头（无自动率定）；继承 D8 单向流的河网走向偏差
  - 资源包络：16B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.strahler`** Strahler 河流分级（`native`·成熟度 已验证，契约: `stream_network`，出处: `strahler1957`）
  - 假设：Strahler 1957：源头 = 1 级；最高上游级唯一 → 同级，并列 → +1；拓扑序 = 高程降序（接收者严格更低；同高程 (row,col) 兜底）；meta 报告 order_distribution 与 max_order
  - 局限：河网输入依赖 accumulation 阈值（见 terrain.streams 局限）；格网平行汇流会高估并列（+1 升级）频率
  - 资源包络：32B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-12，atol=0
- **`terrain.morphometry`** 流域形态量测（`native`·成熟度 已验证，契约: `morphometry_analysis`，出处: `strahler1957`）
  - 假设：面积/周长来自逆 D8 上流域掩膜；周长 = 边界边缘长度和（网格外视作流域外）；basin length = 流域内 MAX upstream 流程长度（最长山脊→出口路径）；form factor = A/L²；elongation = 2√(A/π)/L（Strahler 1957）
  - 局限：basin length 的 MAX 口径对狭长流域外的形状敏感（非主轴拟合）；排水密度继承河网阈值敏感性；pour point 不做河道 snap
  - 资源包络：40B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09

## `terrain_sky_view` — 地平线与天空可视因子

地平线角（逐方位最大正仰角，openness 家族射线行走）与天空可视因子 SVF（Steyn 1980 的 (1/N)Σcos²ψ 口径）；城市通风/日照/辐射与景观开敞度分析输入。

- **`terrain.horizon_angle`** 地平线角（`native`·成熟度 已验证，契约: `terrain_horizon_analysis`，出处: `steyn1980`, `yokoyama2002`）
  - 假设：每方位（罗盘度，自北顺时针）取射线行走 max arctan((z(d)−z₀)/d) 的正仰角（度）；1 像元步长圆整偏移 + 实际米制距离（与 openness 同口径；各向异性感知）；射线遇 nodata/非有限即停；截断（数据外）视作无遮挡（=0，披露）
  - 局限：方位离散 ≤ 360/方位数 的角分辨率（缺省 8 方位 45°）；半径 ≤ 100 像元护栏；半径外地形不参与（遮挡被低估）；数据缝后的地形被视作无遮挡 —— 诚实低估而非发明遮挡
  - 资源包络：24B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.sky_view_factor`** 天空可视因子 SVF（`native`·成熟度 已验证，契约: `terrain_svf_analysis`，出处: `steyn1980`）
  - 假设：SVF = (1/N) Σ cos²(ψ_i)（Steyn 1980）；ψ_i = 等角距方位的地平线角（度）；ψ_i 与 terrain.horizon_angle 共用同一射线行走实现（不重复逻辑）；平地 ψ ≡ 0 → SVF ≡ 1.0（浮点精确）；深洼/封闭谷地 SVF → 0
  - 局限：方位离散：N 方位等角距采样对崎岖天际线的欠采样；半径 ≤ 100 像元护栏；半径外地形不参与天际线；无地球曲率/大气折射修正（局部地形口径）
  - 资源包络：24B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09

## `terrain_slope` — 坡度分析

DEM 坡度。

- **`terrain.slope`** 坡度（`native`·成熟度 已验证，出处: `horn1981`）
  - 假设：3×3 Horn 梯度；度栅格需 z_factor/纬度修正；坡度 = arctan|∇z|（度）；cell_size_x 承接地理栅格 cos(lat) 东西向修正
  - 局限：边界像元 edge 复制延拓（单侧差分）；地理 DEM 未做 cos(lat) 修正时东西向坡度低估 ~cos(lat)；垂直单位非米（英尺 DEM）时需显式 z_factor
  - 资源包络：40B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09

## `terrain_viewshed` — 视域分析

DEM 视域：观察点视线遮挡布尔掩膜、可见比例与可见面积（扇区视线角扫描；无地球曲率/大气折射）。

- **`terrain.viewshed`** 视域分析（`native`·成熟度 已验证，契约: `viewshed_analysis`，出处: `wang_robinson_white2000`）
  - 假设：无地球曲率/大气折射；目标高度默认 0；扇区视线角判据：目标仰角 ≥ 沿途地形运行最大仰角即可见（切切记可见）；观察点高程 = 观察点地形 + observer_height；射线 ~1 像元 bilinear 采样
  - 局限：扇区角离散 ≈ 最大距离处 1 像元弧长（远距目标近似误差 ≤ 半扇区宽）；观察点邻接 nodata 时高程退化为最近有效像元；地理栅格按 cos(lat) 换算米制像元（带向不修正）
  - 资源包络：16B/像元，像元硬上限 50000000
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09

## `terrain_wetness_indices` — 湿润与侵蚀指数

地形湿润指数 TWI（Beven-Kirkby 1979）、水流功率指数 SPI 与 USLE LS 因子（Wischmeier-Smith 1978 / Desmet-Govers 1996）。

- **`terrain.twi`** 地形湿润指数 TWI（`native`·成熟度 已验证，契约: `wetness_index`，出处: `beven_kirkby1979`）
  - 假设：TWI = ln(SCA/tanβ)；SCA = (accum+1)·cell_area/contour_width；等流宽度 = cell_size（y 向）；κ=1 flat 口径（单流向近似，meta 披露）；tanβ 下限 1e-6：平地处 TWI 为截断上界（非物理解）
  - 局限：D8/D∞ 单向累积低估发散坡的 SCA（无多向 κ 分解）；slope 与 accum 网格必须同形对齐（无重采样）
  - 资源包络：24B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.spi`** 水流功率指数 SPI（`native`·成熟度 已验证，契约: `wetness_index`，出处: `beven_kirkby1979`）
  - 假设：SPI = SCA·tanβ（侵蚀/输沙潜势代理）；SCA 口径同 terrain.twi；tanβ 无下限（平地 → SPI 0）
  - 局限：静态地形代理，无降雨/土壤参数（非过程模型）；SCA 单流向近似偏差同 terrain.twi
  - 资源包络：24B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09
- **`terrain.ls_factor`** USLE LS 因子（`native`·成熟度 已验证，契约: `ls_factor_analysis`，出处: `wischmeier_smith1978`, `desmet_govers1996`）
  - 假设：mccool：LS=(λ/22.13)^m·(65.41sin²θ+4.56sinθ+0.065)；m 表 McCool 1987：<1%→0.2、1-3%→0.3、3-5%→0.4、≥5%→0.5；desmet_govers：LS=(m+1)·(SCA/22.13)^m·(sinβ/0.0896)^1.3；SCA 口径同 TWI；λ 建议传 upstream 流程长度（缺省固定 100 m，meta 披露）
  - 局限：标准径流小区经验式的栅格外推（无降雨/植被因子）；n=1.3 固定（Desmet-Govers 1996 实现惯例），不暴露调参
  - 资源包络：24B/像元
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09

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

- **`interpolation.trend_surface`** 趋势面分析（`native`·成熟度 已验证，契约: `trend_surface_analysis`，出处: `webster_oliver2007`，精度: exact）
  - 假设：全局多项式 OLS：z ~ u^i·v^j（i+j≤order），坐标缩放至单位盒（条件数稳定，已披露）；OLS 残差方差 σ̂²=SS_res/(n−p) 是有效的模型方差证据（区别于克里金逐点方差）；bbox 外评估照常输出但逐格标记 extrapolated（趋势模型本就全局外推）
  - 局限：全局多项式只能表达大尺度趋势——局地结构交给克里金/TIN/RBF；高阶多项式边缘振荡（Runge 现象）：order≤3 硬限制；坐标零跨度/设计矩阵不满秩结构化拒绝（DegenerateData）
  - 回退：`interpolation.kriging`→approximation
  - 资源包络：64B/要素
  - 取消：none
  - 数值容差：rtol=1e-09，atol=1e-12

## `triangulation_interpolation` — 三角网插值

Delaunay TIN 三角网插值（linear / clough_tocher），凸包外不外推。

- **`interpolation.tin`** TIN 三角网插值（`native`·成熟度 已验证，契约: `tin_interpolation`，出处: `watson1981`, `clough_tocher1966`，精度: exact）
  - 假设：Delaunay 三角剖分上的分段插值：linear=C⁰ 重心插值，clough_tocher=C¹ 三次；凸包外诚实空缺（fill_value=NaN）：TIN 不外推，格网外的缺失进 metadata；米制坐标下剖分：地理输入经 estimate_utm_crs 自动投影（与 IDW 同 CRS 政策）
  - 局限：样本共线/退化构型结构化拒绝（DegenerateData，附修正提示）；>20 万样本拒绝（Qhull 内存有界但超限先抽稀）；凸包外格网无值——需要全域覆盖时改用 IDW/趋势面（会外推）
  - 回退：`interpolation.idw`→approximation
  - 资源包络：32B/要素，要素硬上限 200000
  - 取消：none
  - 数值容差：rtol=1e-09，atol=0
- **`interpolation.natural_neighbor`** 自然邻域插值（`native`·成熟度 已验证，契约: `natural_neighbor_analysis`，出处: `sibson1981`, `watson1981`，精度: exact）
  - 假设：Sibson (1981) 坐标：权重=插入点窃取的 Voronoi 面积比例（精确多边形裁剪面积）；Watson (1981) 阶梯 walk：外接圆包含格点的单形集合 = 自然邻域（邻接 walk 收集）；精确插值器：过样本点（重合格点直接返回样本值，float64 精确）
  - 局限：凸包外 NaN——不外推（需要全域覆盖时改用 IDW/趋势面）；近共线构型下 Sibson 权重几何呈长条：外墙自适应外扩保证面积精度（次数披露）；>20 万样本 / >400 万目标格点类型化拒绝；逐格点 Python 裁剪成本高
  - 回退：`interpolation.tin`→approximation
  - 资源包络：8B/像元，要素硬上限 200000，像元硬上限 4000000
  - 取消：none
  - 数值容差：rtol=1e-06，atol=1e-09

## `variogram_analysis` — 变异函数分析

方向变异函数 + 6 家族模型选择（加权 RSS + AICc），结构分析统计表输出。

- **`interpolation.directional_variogram`** 方向变异函数（`native`·成熟度 已验证，契约: `directional_variogram_analysis`，出处: `webster_oliver2007`, `isaaks_srivastava1989`）
  - 假设：轴向（双向）配对过滤：方位角 +180° 属同一条轴，曲线逐位一致；方位角为数学约定：0°=东(+x)、逆时针（与 anisotropy_angle 一致，非罗盘）；滞后 bin 与全向 empirical_variogram 同一 span/edges 约定（tolerance=90°、同输入 ≤2000 时两者逐位一致）
  - 局限：单轴单次调用：完整各向异性椭圆需多方位角扫描（本工具不自动拟合椭圆；库级 kriging.fit_anisotropy 提供多方位扫描自动拟合）；带宽过滤为 GSLIB band 语义近似（配对中点到轴线垂距）；统计表输出（无表面）：结果供变异函数建模与各向异性诊断使用
  - 资源包络：对预算 200000，要素硬上限 2000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-09，atol=0
- **`interpolation.variogram_selection`** 变异函数模型选择（`native`·成熟度 已验证，契约: `variogram_selection_analysis`，出处: `webster_oliver2007`, `matern1986`, `cressie_hawkins1980`）
  - 假设：6 家族（spherical/exponential/gaussian/matern/wave/cubic）在同一经验变异函数上同台；加权 RSS 即 fit_variogram 的拟合目标（样本对计数 σ-权重）——与 auto 选型同源；AICc 自由度 k=3（sill/range/nugget）；matern k=4（固定平滑度 ν 计入，已披露）
  - 局限：AICc 基于加权残差而非严格极大似然（信息准则是近似的，已披露）；滞后 bin 数 n ≤ k+2 时 AICc 诚实取 inf（不伪造小样本准则）；统计表输出（无表面）；选中模型需再传入 kriging 工具出表面
  - 资源包络：对预算 200000，要素硬上限 2000
  - 取消：chunk_boundary
  - 数值容差：rtol=1e-09，atol=0

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

## `workspace_snapshot` — 工作空间快照

工作空间快照保存/恢复：产物账本 + 图层引用 + 视图设置，可选物化存活载荷到持久内容库。

- **`workspace.snapshot.durable`** 工作空间快照（保存/恢复）（`native`·成熟度 —）
  - 假设：快照元数据始终落盘；materialize=claimed 时载荷物化到持久内容库
  - 局限：materialize=none 的快照在会话过期后仅元数据可读

## `workspace_state_inspection` — 工作空间状态检视

只读检视会话工作空间（产物账本、图层引用、快照清单）。

- **`workspace.inspection.readonly`** 工作空间状态检视（只读）（`native`·成熟度 —）
  - 假设：只读投影，绝不改工作空间状态

## `zonal_statistics` — 分区统计

面内栅格 min/max/mean/sum 统计。

- **`remote.zonal_stats`** 分区统计（`native`·成熟度 已验证）
  - 假设：统计量在面掩膜内计算（nan-aware）；栅格与面 CRS 一致由上层保证；rasterstats/zonal 统计实现（all_touched=False 惯例）
  - 局限：面跨界像元按像元中心归属（惯例披露）
