# Spatial Algorithm Foundation V3 — 实施计划

> 基线：master@222a994。分支：feat/spatial-algorithm-foundation-v3。
> 审计依据：docs/dev/spatial-algorithm-v3/audit.md。

## 阶段划分（每阶段独立 commit）

### Phase 1 — 契约层修复（audit A1-A7）
- registry.validate() 增加算法输出 ⊆ capability 输出校验；修复 admin.boundary_lookup 错配。
- `algorithms_for_capability(include_planned=True/False)` 行为与命名对齐（过滤 planned+unavailable）。
- ScaleProfile 消费 raster_cells（新增 estimated_bytes 可选字段）；raster 主导算法的 scale_tier 由 cells 决定。
- 修复过时注释（interpolation.py kriging method、terrain D∞ 两处、roughness 引用补注）。
- 为 remote.change.raster 等无元数据描述符补诚实 assumptions/limitations。

### Phase 2 — 空间统计 V3
- **MGWR backfitting**（真逐变量带宽 + backfitting 迭代，非 GWR 改名）：geo_analysis/spatial_regression.py + 工具 run_mgwr + 描述符 native + 契约。
- GeoDetector **生态探测**（t 检验 q 方差比较）与**风险探测**（分层均值比较 + 显著性）。
- GWR 带宽诊断（LOO-CV 曲线元数据）+ 局部共线性诊断（逐点条件数）。
- Gi* permutation p 选项（seed=42，contract v3 additive）。
- Local Join Count（Sokal 1998 / Anselin & Li 2019 无自邻接二元局部检验）。
- 双变量局部 Moran（esda.Moran_Local_BV 诚实委托 + 岛屿权重披露）。
- 空间权重诊断 capability（连通分量/孤岛/对称性/邻居数分布）。

### Phase 3 — 地统计与插值 V3
- indicator kriging（多阈值概率面）。
- collocated co-kriging（Markov Model 1，披露近似）。
- nearest neighbor（Voronoi 值填充）+ natural neighbor（Sibson，scipy Delaunay）。
- directional variogram（方位角/容差分箱）+ variogram model selection（加权 RSS/AIC ranking）。
- block kriging（块支撑离散化平均协方差）。

### Phase 4 — 点格局 / 时空
- space-time K（Diggle et al. 1995，permutation 包络）。
- Mantel 检验（空间-时间距离矩阵相关，permutation seed=42）。
- G/F 边缘校正（border/reduced-sample + isotropic 选项），J 随之升级。
- cross pair correlation g12。

### Phase 5 — 网络 exact backend
- exact p-median / p-center（scipy.optimize.milp；规模上界内 exact，超界诚实拒绝或退化启发式并披露）。
- eigenvector centrality（幂迭代，收敛披露）。
- getis/centrality backend_variants 真双路径声明。

### Phase 6 — 地形
- sky view factor + horizon angle（方位角采样地平线）。
- D8 epsilon flat-routing 选项（epsilon fill 后梯度定流向）。
- 注释/披露修复（D∞ 过时声明、roughness 引用）。

### Phase 7 — 遥感 V3
- MNF（局部差分噪声协方差 → 白化 PCA → 反白化）。
- ICA（sklearn FastICA，确定性 seed）。
- SAM / SID（端元光谱匹配）+ matched filter + RX anomaly。
- MAD / IR-MAD（CCA + 迭代重加权，迭代次数/收敛披露）。
- 分割基础（spatial-spectral seeded KMeans，确定性）。
- 端元提取 VCA（Nascimento & Dias 2005，EXPERIMENTAL）。
- 波段相关性矩阵（stats_table 输出）。
- 时间特征提取（per-pixel min/max/amp/phase/seasonal）。
- 鲁棒归一化（分位匹配跨波段/跨期）。
- 云 QC 基础（亮度阈值热云掩膜，EXPERIMENTAL + Fmask 声明 planned 边界）。

### Phase 8 — SAR V3
- 逐像元入射 LUT 定标（incidence 2D 数组通道；contract v2 additive）。
- 热噪声去除（noise floor 标量/LUT 通道；诚实披露 Sentinel-1 GRD LUT 语义需求）。
- 多时相 speckle 滤波（intensity 域 MT-Lee，披露非 Quegan SLC 谱域）。
- 相干性估计（复数 SLC 对 I/Q 通道输入，EXPERIMENTAL）。
- Gamma Map / Kuan 滤波（speckle filter 枚举扩展）。
- RTC 地形辐射校正（局部入射角 γ-flattening）。
- layover/shadow 掩膜（局部入射角几何判据）。
- log scaling 形式化（amp↔intensity↔dB 转换工具）。
- ENL 估计图（滑动窗口 ENL）。

### Phase 9 — Backend/Scale 统一
- select_backend 消费 raster_cells/estimated_bytes；诊断输出保持形状。
- Gi*（normal/permutation）、speckle filter 枚举等真实双路径声明 backend_variants。

### Phase 10 — Numerical Oracle Corpus
- scripts/gen_science_oracles.py 生成 tests/science_oracles/data/*.json（期望值由 scipy/sklearn/esda/手解析一次性生成）。
- tests/science_oracles/ 分域回放测试（parametrize）。
- 目标 ≥1000 用例；覆盖 CRS/单位/空输入/退化几何/nodata/种子确定性/病理值/高纬/反子午线/无效几何/极小极大数据/回归锚点。

### Phase 11 — 文档与目录
- docs/science/FOUNDATION_V3.md（架构/契约/算法/兼容性/限制/后续）。
- 重生成 ALGORITHM_CATALOG.md（byte parity 测试通过）。
- 更新 docs/science/architecture.md 增量说明。

### Phase 12 — Review gate + PR
- 7 维 review（science/CRS/numerical/performance/architecture/regression/docs）。
- 修复 blocking → 分批本地验证 → 分阶段 commit → push → PR。

## 规模预期（诚实口径）
- algorithms: 128 → ~180+（新描述符全部带完整 VNext 元数据 + conformance）
- capabilities: 88 → ~120（仅真实新族，不复制既有能力语义）
- contracts: 62 → ~100（新算法各带契约；vN additive 升级不改语义）
- oracle cases: ≥1000（生成式 fixture，确定性回放）
