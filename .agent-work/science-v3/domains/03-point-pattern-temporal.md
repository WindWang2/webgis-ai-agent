# 03 Point Pattern & Spatiotemporal 审计

审计对象 worktree：`/home/kevin/projects/webgis/webgis-ai-agent-science-v3`（只读审计，2026-09-07）。
范围：`app/lib/gis/algorithms/point_pattern.py`（12 descriptor）+ `statistics.py` 中 `stats.st_dbscan`（=13 个 point_pattern 域）与 `app/lib/gis/algorithms/temporal.py`（8 个 temporal descriptor）；实现层 `app/lib/geo_analysis/point_pattern.py`（1686 行）、`app/services/temporal/trend.py`（869 行）等；工具层 `app/tools/point_pattern_tools.py`、`spatial_stats.py`、`temporal_tools.py`、`spatial.py`。

**总体结论：无 fake-native 核心实现。** 全部 12 个点格局函数的数学内核真实且与文献一致（逐条核对：各向同性边缘校正含角点重叠公式、池化成对表 random-labelling、Knox E=2·ST/(n(n−1))、K_st 独立参考 πr²·2t、Mantel 标准化 r 同流置换、MK tie 校正方差+连续性校正、CUSUM max 统计 bootstrap、经典中心 MA 分解）。确定性契约（seed=42、`default_rng` 单流、+1 校正秩检验）实现与声明一致，且被逐位相等测试钉住（`a == b` 断言）。实测 57/57 测试通过（`pytest tests/unit/lib/test_point_pattern_{science,v2,v3}.py test_space_time_interaction.py tests/unit/test_temporal_science_vnext.py`）。51 条 conformance_tests 引用（文件+函数名）全部实存。缺口集中在：`temporal.hotspot`/`temporal.aggregate` 两个 descriptor 元数据失实或缺失、per-radius p 值族无多重校正、工具层未暴露 `window` 参数、本域零 `backend_variants`/复杂度声明、EHA（emerging hotspot）能力整体缺失。

---

## 1. Census 表

图例：status=descriptor.scientific_status；uncertainty = uncertainty_outputs；契约=parameter_contract_ref（id@version）；variants=backend_variants 声明数。

### 1a. Point pattern 域（13 个）

| id | name | status | tools | 实现位置 | 契约 | references | uncertainty | tolerance | variants | conformance | 测试覆盖 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| point_pattern.ripley_k | Ripley's K 函数 | VALIDATED | ripley_k_analysis（spatial_stats.py:361，经 SpatialAnalyzer） | app/lib/geo_analysis/point_pattern.py::ripley_k（L229） | ripley_k_analysis@2 | ripley1976 | — | 有（CSR 包络内/格网 K≤πr²） | 0 | 4 条全实存 | test_point_pattern_science.py 5 测试（CSR 包络、格网排斥、类型化拒绝、确定性） |
| point_pattern.quadrat_test | 样方 χ² 离散检验 | VALIDATED | quadrat_analysis（spatial_stats.py:408） | 同上::quadrat_test（L374） | quadrat_analysis@1 | — | statistical_significance | 隐含（手算分支） | 0 | 2 条实存（文件另有 2 条补测） | test_point_pattern_science.py 4 测试（单角拒绝 CSR、CSR 不显著、双侧 regular/clustered 分支） |
| point_pattern.nni | 最近邻指数（NNI） | VALIDATED | nearest_neighbor（app/tools/spatial.py:224） | app/lib/geo_analysis/statistics.py::calculate_nearest（L1006） | —（无契约） | clark_evans1954 | statistical_significance | 隐含（手算方格） | 0 | 5 条实存 | test_point_pattern_v2.py NNI 3 测试 + test_nearest_contract.py 2 测试 |
| point_pattern.dbscan | DBSCAN 密度聚类 | VALIDATED | spatial_cluster（spatial_stats.py:92） | statistics.py::cluster_narrated(method=dbscan) | — | ester_kriegel1996 | — | — | 0 | 2 条实存 | tests/unit/test_spatial_stats.py DBSCAN 2 测试 |
| point_pattern.g_f_j | G/F/J 距离函数 | VALIDATED | g_f_j_analysis（point_pattern_tools.py:112） | point_pattern.py::g_f_j_functions（L568，V3 增 border/isotropic 校正） | g_f_j_analysis@2（含 edge_correction） | diggle1983, van_lieshout_baddeley1996, ripley1976 | monte_carlo_summary + statistical_significance | 有 | 0 | 4 条实存 | test_point_pattern_v2.py GFJ 5 测试 + v3 边缘校正 2 测试 |
| point_pattern.pcf | 成对相关函数 g(r) | VALIDATED | pcf_analysis（point_pattern_tools.py:213） | point_pattern.py::pcf + _pcf_from_k（L721） | pcf_analysis@1 | illian2008, ripley1976 | MC + significance | 有（CSR 均值≈1±0.2） | 0 | 3 条实存 | test_point_pattern_v2.py pcf 3 测试 |
| point_pattern.cross_k | 双变量交叉 K | VALIDATED | cross_k_analysis（point_pattern_tools.py:301） | point_pattern.py::cross_k（L868，池化成对表共享 w_inv） | cross_k_analysis@1 | besag1977, ripley1976 | MC + significance | 有 | 0 | 3 条实存 | test_point_pattern_v2.py cross-K 3 测试（类型数/零假设/分离显著） |
| spatiotemporal.knox | Knox 时空交互检验 | VALIDATED | knox_analysis（point_pattern_tools.py:401） | point_pattern.py::knox_test（L1035，query_pairs 稀疏化） | knox_analysis@1 | knox1964 | MC + significance | 有（4 点手算例） | 0 | 7 条实存 | test_space_time_interaction.py 9 测试（含工具层证据块断言） |
| point_pattern.ripley_k_env | K + CSR 模拟包络 | VALIDATED | ripley_k_envelope_analysis（point_pattern_tools.py:776） | ripley_k(envelopes>0)（同一 `_k_curve` 估计器） | ripley_k_envelope_analysis@1 | ripley1976 | MC + significance | 有 | 0 | 2 条实存 | test_point_pattern_v2.py envelope 2 测试 |
| point_pattern.space_time_k | 时空 K 函数 K_st(r,t) | VALIDATED | space_time_k_analysis（point_pattern_tools.py:530） | point_pattern.py::space_time_k（L1209） | space_time_k_analysis@1 | diggle1995, ripley1976 | MC + significance | 有（聚集 fixture 显著/洗牌不显著） | 0 | 2 条实存 | test_point_pattern_v3.py 2 测试 |
| point_pattern.mantel | Mantel 时空检验 | VALIDATED | mantel_test_analysis（point_pattern_tools.py:611） | point_pattern.py::mantel_test（L1394） | mantel_analysis@1 | mantel1967 | MC + significance | 有（r>0.5 显著/洗牌消失/逐位一致） | 0 | 2 条实存 | test_point_pattern_v3.py 2 测试 |
| point_pattern.cross_pcf | 双变量 g12(r) | VALIDATED | cross_pcf_analysis（point_pattern_tools.py:683） | point_pattern.py::cross_pair_correlation（L1511） | cross_pcf_analysis@1 | illian2008, besag1977 | MC + significance | 有（包络覆盖 1/逐位一致） | 0 | 2 条实存 | test_point_pattern_v3.py 2 测试 |
| stats.st_dbscan | 时空 DBSCAN | VALIDATED | st_dbscan（spatial_stats.py:723）+ spatial_cluster | statistics.py::st_dbscan_narrated；spatiotemporal.py 再包装 | — | ester_kriegel1996 | — | — | 0 | 2 条实存 | tests/unit/test_st_dbscan.py 2 测试 |

### 1b. Temporal 域（8 个）

| id | name | status | tools | 实现位置 | 契约 | references | uncertainty | tolerance | variants | conformance | 测试覆盖 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| temporal.profile | 时间画像 | VALIDATED | temporal_profile（temporal_tools.py:174） | services/temporal/profiler.py | — | — | — | — | 0 | 1 条实存 | test_temporal_gis_runtime.py |
| temporal.aggregate | 时间聚合 | **""（未声明）** | temporal_aggregate（temporal_tools.py:249） | services/temporal/aggregation.py | — | — | — | — | 0 | **0 条** | 仅运行时测试间接覆盖 |
| temporal.trend | 时序趋势（ols_sen/MK/季节 MK） | VALIDATED | temporal_trend（temporal_tools.py:334） | services/temporal/trend.py::analyze_trend + mann_kendall/seasonal_mann_kendall（L460-672） | temporal_trend_analysis@1 | sen1968, mann1945, kendall1975 | statistical_significance | 隐含（手算锚点） | 0 | 5 条实存 | test_temporal_science_vnext.py MK/SMK 5 测试（暴力复核 S/Var、scipy 对比、季节跳过披露） |
| temporal.changepoint | CUSUM 均值变点 | VALIDATED | temporal_changepoint（temporal_tools.py:558） | trend.py::cusum_change_point（L675） | temporal_changepoint_analysis@1（bootstrap_draws+seed） | —（无 method_references） | statistical_significance | 有（同 seed 逐位） | 0 | 2 条实存 | test_temporal_science_vnext.py CUSUM 2 测试（±3 定位、无 shift、确定性） |
| temporal.seasonal_decompose | 经典季节分解 | VALIDATED | temporal_seasonal_decompose（temporal_tools.py:647） | trend.py::seasonal_decompose_narrated（L746） | seasonal_decompose_analysis@1 | makridakis1998 | — | 有（正弦+斜坡精确 1e-9） | 0 | 3 条实存 | test_completeness_v3.py 3 测试 |
| temporal.change | 时序变化 | VALIDATED | temporal_change（temporal_tools.py:295） | services/temporal/change.py | — | — | — | — | 0 | 1 条实存 | test_temporal_gis_runtime.py |
| temporal.hotspot | 时空热点 | **""（未声明）** | spatiotemporal_hotspot（temporal_tools.py:434） | **实为 ST-DBSCAN 包装**（engine.py:331 → SpatiotemporalClusterEngine → st_dbscan_narrated）——与 descriptor assumptions（时间片×空间箱计数矩阵）**不符**，见 F1 | — | — | — | — | 0 | **0 条** | test_temporal_profiler.py 间接 |
| temporal.raster_ts | 时序栅格 | VALIDATED | temporal_raster（temporal_tools.py:475） | services/temporal/raster.py | — | — | — | — | 0 | 1 条实存 | test_temporal_gis_runtime.py mock |

---

## 2. Fake-native / 声明不实 findings

- **F1 [MEDIUM] descriptor↔实现语义失配：`temporal.hotspot`**。assumptions 声称"时间片 × 空间箱计数矩阵（描述性）"、limitation 称"箱宽选择敏感"，但工具 `spatiotemporal_hotspot` 实际走 `execute_spatiotemporal_hotspot` → `SpatiotemporalClusterEngine.cluster` → **ST-DBSCAN**（eps_spatial_m/eps_temporal_days/min_samples 参数即 ST-DBSCAN 语义）。声明的算法与执行的算法不是同一个；且无 conformance test、无 scientific_status、工具不挂 scientific_evidence。属于"声明不实"（非结果造假——ST-DBSCAN 本身真实），应改写 descriptor 或落地真正的箱计数实现。
- **F2 [LOW] `temporal.aggregate` descriptor 完全裸奔**：无 scientific_status、无 conformance_tests、无 assumptions。能力真实存在但科学元数据为零，登记门槛未达（registry 只对 VALIDATED 强制 conformance，空 status 绕过了门）。
- **F3 [LOW] `stats.geodetector` descriptor 反向缩水**：实现（`geodetector_narrated`，statistics.py:2709）含 Wang 2010 q 统计、F 检验、固定种子置换、交互五分类、MonteCarloSummary，深度合格；但 descriptor 只有骨架字段（无 method_references/assumptions/limitations/crs_class）。是"实现>声明"的诚实性缺口，会让证据块引用空头 descriptor。
- **F4 [INFO] 叙事超载：`spatiotemporal_hotspot` 工具描述**称"时空持续/偶发热点发现"，暗示 Emerging Hotspot 语义（持续/新增/消退分类），实际只输出 ST-DBSCAN 簇计数。全仓（app/+docs/）grep `emerging` 无任何 EHA 实现——没有谎称实现，但话术超出产出。
- **F5 [INFO] 契约参数工具侧不可达**：`ripley_k_analysis` 契约 v2 登记了 `envelopes`（0=关），但工具签名不暴露该参数，包络只能经 `ripley_k_envelope_analysis` 走；lib 层 `window`（固定研究域）在 ripley/quadrat 工具层均未暴露，bbox 自归一化局限在 descriptor 有披露但调用方无法绕开。
- **F6 [INFO] 工具元数据漂移**：V3 的三个工具（`space_time_k_analysis`/`mantel_test_analysis`/`cross_pcf_analysis`）的 @tool 装饰器缺 `side_effect/deterministic/network/latency_class/memory_class/scale_class/output_semantic_type` 等兄弟工具全部具备的字段（依赖注册表默认值），同域不一致。
- **核心实现零 fake-native 判定**：K/L（各向同性 w_ij 含角点 inclusion–exclusion，公式与 Ripley 1977/Goreaud & Pélissier 1999 一致）、cross-K 池化成对表（避免强分离数据下置换包络塌缩——实现注释与做法都对）、K_st 有序对 w_i^{-1}+w_j^{-1} 等价展开、Mantel 单一置换流同时重标 i/j 两侧、MK tie 项 Σt(t−1)(2t+5)、CUSUM p=(1+count)/(draws+1)、经典分解首尾 None 语义——逐项核对全部正确。诚实的"无 p 值/描述性"输出、配对预算先估后拒（count_neighbors → ResourceScaleMismatch）、NaN/重复点/全同时间戳的类型化拒绝，均属实。

## 3. 契约缺口

1. **多重比较**：`StatisticalSignificance.multiple_testing` 在本域所有工具输出中均为空串；`ripley_k_envelope_analysis` 输出 `K_p_values` 是 n_steps 个并行 p 值，无 BH/Bonferroni/DCLF 全局统计量（pcf/cross_k/cross_pcf 有 sup 统计量全局检验，K 族反而没有）。
2. **`window` 参数**未达工具层（见 F5）——bbox 缺省是本域多数方法最大的可操作偏差源。
3. **permutations 为字符串枚举**（"199"/"499"）而非整数——API 类型赘疣，跨域不一致。
4. **Knox/Mantel 无 "less" 单侧备择**（时空抑制/near-repeat 规避方向不可检验）；Mantel 无 partial Mantel。
5. **NNI 无契约**（`parameter_contract_ref` 缺失），R 阈值 0.7/1.3 分档无参数化。
6. **域契约聚合缺陷（代码自述）**：`iter_contract_packs` 聚合不到域契约，工具靠显式 `apply_contract(id)` 兜底——中央校验门（parity）对本域契约为弱覆盖（point_pattern.py L465-468 注释）。
7. **temporal.trend 的 input_artifact_types=["stats_table"]** 与工具实收 GeoJSON features 不一致。
8. **ST-DBSCAN（stats.st_dbscan）无 uncertainty_outputs、无 min_samples/eps 敏感性披露之外的检验语义**——descriptor 自己承认"无自动带宽"，缺 envelope/稳定性诊断。

## 4. 数值风险图

| 位置 | 风险 | 等级 | 缓解现状 |
|---|---|---|---|
| `_pcf_from_k`（point_pattern.py:721-729） | 用户给定过小 bandwidth → Epanechnikov 权重行全 0 → 0/0=NaN 直接进 JSON 输出（只校验 <r_max，未校验 ≥一个 r 步宽） | **P1** | 无守卫；测试只覆盖过大带宽 |
| `np.gradient` 端点单侧差分 | g(r)/g12(r) 首末半径导数偏倚；cross_pcf 的 tendency 取 `g12[0]>1` 恰在最biased 的首格 | P2 | limitations 只泛述"分辨率受限" |
| `_isotropic_inside_fraction` | 角点 1/w 可至 1/1e-9=1e9 量级；被 r_max≤半窗比例封顶约束保护，但极端贴角点对仍产生高杠杆权重 | P2 | r_max 上限 0.5 + budget 闸 |
| `K_p_values` 逐半径秩检验族 | 同一包络上 n_steps 个相关 p 值，无族校正 → 5% 水平族假阳性膨胀 | P2 | 无 |
| G/F 包络 p 只在 r_max 一点评测 | 全曲线偏离不可见（"G_p 在 r_max"有披露但易被当成整体结论） | P2 | p_method 文本披露 |
| quadrat 双侧 p=2·min(p_up,p_low) | χ² 分布不对称，倍增法非精确双侧（保守方向） | P3 | 注释 M1 披露、有分支测试 |
| MK/Sen `_MAX_N=1024` 确定性子采样 | 超限序列 p 值为近似（stride 采样） | P2 | 警告在场 + 测试 |
| K_st 时间维无边缘校正 | 窗端 Δt 截断，t_max=半跨度下 ref πr²·2t 亦受截断影响，结论对窗长敏感 | P2 | temporal_edge_note 诚实披露 |
| Mantel 全点对当独立 | 空间自相关下 p 偏乐观 | P2 | disclosure 在场 |
| 输出 round(4/6 位) | 报文舍入（测试相应使用容差）；`J` 的 NaN 序列化为字面 `"NaN"` 字符串 | P3 | 一致且被测试锚定 |

## 5. 规模/后端缺口

- **上限声明真实且先拒后分配**：O(n²) 族 n≤20,000 + 配对预算 5e7（count_neighbors 估算）；Mantel n≤2000（密集矩阵 32MB 量级披露）；Knox/K_st 20,000；F 查询格 4n∧2000。全部有测试（monkeypatch 顶闸 + 2001 点拒绝）。
- **backend_variants：本域 21 个 descriptor 声明数为 0**。工具层 `_backend_diagnostic` 只产出 "variant=(default)" 的诊断文本——机制诚实（`select_backend` 对无变体算法返回默认路径），但等于本域没有 scale 分层实现（无分块 K、无流式置换、无大 n 近似变体）。envelope 计算（最多 499×n² 次估计器重放）为顺序单线程循环，`preferred_execution_policy=THREAD` 只是 harness 级并发声明，envelope 内部未并行。
- CSR 包络重放内存安全：每次 draw 重建 tree+COO，受同一预算约束（`_k_curve` 内纵深防御已兜底）——正确但无增量复用（例如共享 pair-distance 排序表对模拟不可行，属合理代价）。

## 6. 不确定性缺口（permutation 数、CI、FDR）

1. 默认置换数偏少：cross_k/knox/cross_pcf/`space_time_k` 默认 199（p 分辨率 1/200）；`mantel` 默认 499；上限 499（Knox/Mantel 999）。对 0.01 显著性声明分辨率不足，建议默认 499、上限 999。
2. **无任何 CI/区间输出**：K/g/L 曲线只有包络分位（p5/p50/p95），无 simultaneous envelope（全部包络取 max/min 的 global envelope，Diggle 版）——逐半径包络 + 逐半径 p 的组合在族水平无控制。
3. **FDR 缺位**：本域全部 `multiple_testing=""`（对照：同仓 spatial_statistics 域 local_geary/LJC/bivariate_local_moran 默认 bh）。最小修复：对 `K_p_values`（及 G/F 逐半径扩展时）做 BH 并填 `multiple_testing="BH-FDR"`。
4. Knox 平局（Δt=0）披露在场，但未提供含 tie 的精确置换（explict tie handling / Kröger 精细检验）。
5. `temporal.changepoint` bootstrap_draws 100–1000、seed 可调（好），但 seed 进了契约而 point-pattern 族 seed 硬编码 42 不可调——固定种子策略一致，牺牲了敏感性重跑能力（可通过多 seed 对照诊断，目前不可达）。
6. LISA（h3_lisa，statistics.py:1276）只有期望假阳性计数披露（0.05n），无 FDR 校正——与 local_geary（默认 bh）域内双标。

## 7. 参考文献验证

`app/lib/gis/method_references.py` 中本域引用的 14 个键全部实存且为**完整规范引文**（抽查：ripley1976 = J. Applied Probability 13(2):255-266；clark_evans1954 = Ecology 35(4):445-453；van_lieshout_baddeley1996 = Statistica Neerlandica 50(3)；benjamini_hochberg1995 = JRSS-B 57(1):289-300；diggle1983/van_lieshout_baddeley1996/illian2008/besag1977/knox1964/mantel1967/diggle1995/makridakis1998/mann1945/sen1968/hirsch_slack1982 均在册）。无占位符、无虚构 DOI。方法引用与实现的一致性抽查通过：diggle1995 ↔ K_st(r,t) 独立性参考与时置换（Diggle et al. 1995 确用 time-randomisation）；hirsch_slack1982 ↔ 逐季 S/Var 池化；makridakis1998 ↔ 经典 MA 分解。缺口：`temporal.changepoint` 无 method_references（应补 cusum 源流，如 Page 1954 / standard CUSUM bootstrap 文献）；`temporal.hotspot`/`quadrat_test` 无引用（quadrat 可补 Greig-Smith 1952/1964）。

## 8. V3 建议清单

| # | 建议 | 方法引用 | 现状差距 | 可行性（libpysal/esda） | 实现位置 | 测试策略 | 优先级 |
|---|---|---|---|---|---|---|---|
| R1 | **Emerging Hotspot Analysis（时空热点演化）**：时空 bin（H3×period）上逐期 Gi*（已有 `hotspot_narrated`/`hotspot_gistar`）+ 对每 bin 的 z 时序跑已有 `mann_kendall`，分类 17 型 EHA（new/consecutive/intensifying/persistent/diminishing/sporadic/historical…） | Getis & Ord 1992; ESRI EHA (Nelson et al. 2014)；MK 已在册 | **整体缺失**（F4：话术已出现、能力为零）；descriptor `temporal.hotspot` 语义失配（F1）可借此落地 | 高：Gi* 与 MK/Seasonal-MK 均为本仓已验证组件，esda 非必需；binning 复用 h3_binning/heatmap_grid | `app/lib/geo_analysis/statistics.py`（新 `emerging_hotspot_narrated`）或新模块 `spatiotemporal_eha.py`；工具挂 `app/tools/spatial_stats.py`；descriptor 修 `temporal.hotspot` 或新增 | 手算 fixture：2 期 3 bin 构造 new/persistent/diminishing 各一（z+MK 手算）；固定种子置换校准（随机数据 EHA 率）；分类互斥完备性 | **P0** |
| R2 | **local Geary 族扩展（多变量 local Geary）**：C_i^{MV}=Σ_φ Σ_j w_ij(z_φi−z_φj)² | Anselin 1995; Anselin 2019（local Geary MV） | 仅有单变量 `stats.local_geary` | 高：`esda.Geary_Local_MV` 已存在，可直接委托（与 bivariate_local_moran 同模式） | `app/lib/geo_analysis/statistics.py` + descriptor `stats.local_geary_mv` + 工具 `local_geary_mv`（spatial_stats.py） | 与 esda.Geary_Local_MV 同权重逐位一致（委托测试）+ x=y 退化为单变量锚点 | P1 |
| R3 | **bivariate local Moran**：**已实现**（`stats.bivariate_local_moran`，esda.Moran_Local_BV 委托，工具 bivariate_local_moran spatial_stats.py:1682，conformance 在 test_spatial_stats_v3）。残余缺口仅：esda 缺失时的 ImportError 降级路径与 Wartenberg 因果警示的前端披露 | wartenberg1985; anselin1995 | 已闭合 95% | 高（已委托） | 既有 | 已有 labels+determinism 测试 | P2（收尾） |
| R4 | **local join count 扩展（多色/高阶）**：k 类多色 J（multicolour LJC）与 smooth/order-2 邻接 | Anselin & Li 2019 (revised); Sokal 1998 | 已有单色 LJC + bivariate join count；多色缺 | 中高：esda 无现成多色 LJC，需自实现（条件置换框架可复用 LJC 既有代码，保持 1 总数置换改为类置换） | statistics.py（`local_join_count_narrated` 旁） | 手算 3×3 三色 fixture；置换确定性；与单色实现退化一致 | P2 |
| R5 | **spatial stratified heterogeneity 扩展**：q 最优分层搜索（离散化扫描：quantile/equal/kmeans/jenks 各自 q 最大化选层）+ SH 检验的方差比 RV/一致性 | Wang et al. 2010; Wang & Xu 2017 (GESH) | `geodetector_narrated` 已有 q+F+置换+交互+ecological+risk 探测器；缺自动最优离散化（bins 固定）与 descriptor 元数据（F3） | 高：纯 numpy/pandas 可实现；无新依赖 | statistics.py::geodetector_narrated 加 `optimal_bins` 分支；补 descriptor 元数据 | 手算 q（2 层已知方差）锚点；最优层单调性（q 最优 ≥ 各固定 bins）；确定性 | P2 |
| R6 | K 族全局 DCLF 检验 + 逐半径 BH：`ripley_k_envelope_analysis` 增加 sup|K−πr²| 秩 p（与 pcf 同式）并对 `K_p_values` 填 BH | Diggle 1979/DCLF; benjamini_hochberg1995 | 只有逐半径 p，无全局统计量（§6.1） | 高：包络曲线已在内存，sup 免费获得 | point_pattern.py::ripley_k envelopes 分支 + 工具 uncertainty 块 | CSR fixture 全局 p>0.05；聚集 fixture sup p<0.05；BH q 单调性 | **P1** |
| R7 | 工具层暴露 `window` 与 `envelopes`（ripley_k_analysis/quadrat_analysis），契约升级 additive | — | F5 | 高 | spatial_stats.py 两工具 + 契约 v3 | 默认不传时输出逐位不变（回归锚点）+ 传 window 的单角样方拒绝 CSR 用例 | P1 |
| R8 | 时空 K 时间边缘校正（平移零假设/时间原点随机平移 envelope）与逐 (r,t) 网格包络输出 | diggle1995; Iftimi et al. 2019 | temporal_edge_note 仅披露 | 中：需实现 translation estimator，注意与现 ref 的兼容开关 | point_pattern.py::space_time_k | 已知构造 fixture 对照现实现方向一致；平移包络覆盖 CSR | P2 |
| R9 | descriptor 修复包：改写/落地 `temporal.hotspot`（F1）、补 `temporal.aggregate`/`stats.geodetector` 元数据（F2/F3）、V3 三工具 @tool 元数据补齐（F6）、changepoint 补 method_references | — | §2 | 高 | algorithms/temporal.py、statistics.py、temporal_tools.py | registry parity 门 + descriptor 覆盖门测试 | **P0（低成本高杠杆）** |
| R10 | pcf/g12 直接成对核（Stoyan）估计器作为可选 `estimator="stoyan"`，绕开 K 导数端点偏倚；加 bandwidth ≥ 半步宽守卫 | illian2008 (Stoyan & Stoyan 1994) | §4 P1 NaN 风险 + 端点偏倚 | 中高 | point_pattern.py::_pcf_from_k | 微小带宽 → 类型化拒绝测试；Stoyan vs K-derivative 在 CSR 上均 g≈1 | P1（守卫部分 P0 级一行修复） |

## 9. 现有算法增强建议

1. **ripley_k**：输出增 `L_minus_r`（L(r)−r）与 `delta_K`；`tendency` 文本与 p 值联动（当前 envelopes 下 tendency 仍只看逐半径计数）。
2. **g_f_j**：包络 p 从"仅 r_max"扩为逐半径 + BH；为 J 曲线补包络（现只有 G/F 包络，J 是主解读量却无显著性）。
3. **cross_k**：同时输出 K12 与 K21（当前只算方向 1→2；random labelling 下两者期望相等，方向不对称本身就是诊断量）；`n_pairs` 已披露，可加方向计数。
4. **knox**：增加备择 `alternative ∈ {greater, less, two-sided}`；输出空间/时间阈值的敏感性 mini-sweep（阈值 ±25% 三点对照，自动披露）。
5. **mantel**：n>2000 时提供 chunked/采样近似路径而非直接拒绝（置换相关性可分块累积）；加 Spearman ρ 版本。
6. **space_time_k**：`perm_sup_quantiles` 补 p50/p95 之外的三维 argmax 定位输出（哪个 (r,t) 超包络）——已是网格，边际成本极低。
7. **NNI**：加可选固定种子蒙特卡洛包络（替代正态近似，descriptor limitations 已自认偏差源）；R 阈值经验分档文本化为建议而非判语。
8. **quadrat**：多粒度 sweep（2×2..10×10 一次运行，输出 VMR/χ² 谱），descriptor limitations "粒度敏感"从披露升级为工具能力。
9. **temporal.trend**：实现 Yue-Pilon TFPW 预白化选项（当前"无预白化"披露）；SMK 支持对高频原始序列自动聚合到月/季（现要求逐点日期）。
10. **temporal.changepoint**：加二分分割（binseg）多变点选项（单均值漂移之外的诚实扩展），或明确文档指引多次窗口滑动调用。
11. **st_dbscan / dbscan**：eps 敏感性诊断（k-dist 图数据输出）+ 簇稳定性（子采样重跑 Jaccard），补 `uncertainty_outputs`。
12. **确定性契约硬化**：把 seed 从硬编码 `_FIXED_SEED` 提升为可选参数（缺省 42 逐位不变），使"多 seed 稳定性对照"成为可能，同时保持现有 conformance 不变。
