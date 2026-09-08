# 01 Spatial Statistics 审计

审计范围：`app/lib/gis/algorithms/statistics.py`（27 个 descriptor + 22 个 PARAMETER_CONTRACTS）、实现层 `app/lib/geo_analysis/{statistics,spatial_regression,spatial_weights}.py`、工具层 `app/tools/spatial_stats.py`、测试 `tests/unit/lib/test_{spatial_stats_v3,local_spatial_stats_v2,spatial_regression_v2,spatial_stats_conformance,statistics_hardening}.py` + `test_completeness_v3.py`。
验证方式：全量读实现层三文件 + descriptor 全文 + 工具层关键函数；63 个 conformance_tests 引用节点逐一 import 校验（全部实存）；`test_spatial_stats_conformance.py + test_spatial_stats_v3.py` 实跑 **27 passed**（41.7s）。

## 1. Census 表

| id | name | status | tools | 实现位置 | 契约 | references | uncertainty | tolerance | variants | conformance | 测试覆盖 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| profile.spatial.stats | 空间数据画像 | VALIDATED | spatial_stats, webgis_source_profile | tools 层描述符路径 | —（描述性） | — | — | — | 无 | 1（test_descriptor_derived_profile_688） | 有 |
| stats.category.breakdown | 类别构成统计 | **未声明** | spatial_stats | tools 层 | — | — | — | — | 无 | 无 | 间接 |
| spatial.hotspot.local | Gi* 局部热点 | VALIDATED | hotspot_analysis | statistics.py:785 `hotspot_narrated`（normal 路径经 SpatialAnalyzer.hotspot 委托同函数） | gi_star_analysis v1 | getis_ord1992, BH1995 | statistical_significance | "atol 5e-5（既有 conformance）" | 无 | 4 | 有（含置换/scale-guard） |
| stats.morans_i | 全局 Moran I | VALIDATED | moran_i | statistics.py:374 | moran_i_analysis v1 | moran1950, BH1995 | sig | vs esda <1e-10 | 无 | 5 | 有（checkerboard/-1、esda 对齐、种子 p） |
| stats.gearys_c | 全局 Geary C | VALIDATED | geary_c | statistics.py:499 | geary_c_analysis v1 | geary1954 | sig | vs esda <1e-10；C=2−2/n 精确 | 无 | 3 | 有 |
| stats.general_g | General G | VALIDATED | general_g | statistics.py:645 | general_g_analysis v1 | ord_getis1995 | sig | vs esda.G <1e-10 | 无 | 3 | 有（负值拒绝） |
| stats.h3_lisa | H3 LISA | VALIDATED | h3_lisa | statistics.py:1276（esda.Moran_Local 委托+孤岛中性化） | —（无契约） | anselin1995 | sig（声明） | —（未声明） | 无 | 3 | 有（标量参考/常量拒绝/孤岛） |
| stats.h3_hotspot | H3 Gi* | VALIDATED | h3_lisa（**名不副实**，见 F-1） | statistics.py:785 `hotspot_narrated`（q_value_fdr 仅此路径） | —（无契约；gi_star_analysis 邻近） | getis_ord1992, BH1995 | sig（声明） | — | 无 | 3 | 有（经 hotspot 路径） |
| stats.st_dbscan | 时空 DBSCAN | VALIDATED | st_dbscan, spatial_cluster | statistics.py:1525（SpatialAnalyzer 委托） | — | ester_kriegel1996 | — | — | 无 | 2 | 有（basic/insufficient） |
| stats.local_geary | 局部 Geary C_i | VALIDATED | local_geary | statistics.py:1702 | local_geary_analysis v1 | anselin1995, geary1954, holm1979, BH1995 | sig | vs esda.Geary_Local <1e-8 | 无 | 4 | 有（golden/esda/校正/对抗） |
| stats.join_count | Join Count | VALIDATED | join_count | statistics.py:1841 | join_count_analysis v1 | cliff_ord1973, moran1950 | sig | 计数整数精确 | 无 | 2 | 有（golden/非二值/方差简并诚实披露） |
| stats.bivariate_join_count | 双色 Join Count | VALIDATED | bivariate_join_count | statistics.py:2082 | bivariate_join_count_analysis v1 | cliff_ord1973 | sig | 整数精确；seed42 可复现 | 无 | 3 | 有（手算 fixture） |
| stats.rate_smoothing | EB 率平滑（Marshall MOM） | VALIDATED | rate_smoothing | statistics.py:2321 | rate_smoothing_analysis v1 | marshall1991 | —（确定性） | fixture 精确 | 无 | 3 | 有（常数率/收缩方向/零人口） |
| stats.bivariate_moran | 双变量 Moran | VALIDATED | bivariate_moran | statistics.py:2551 | bivariate_moran_analysis v1 | wartenberg1985, moran1950 | sig | x=y≡单变量 <1e-12；esda <1e-9 | 无 | 2 | 有（属性锚+esda） |
| stats.geodetector | 地理探测器（因子+交互） | VALIDATED | geodetector | statistics.py:2709 | geodetector_analysis v1 | wang2010 | sig + monte_carlo | — | 无 | 2 | 有（q/交互类/对抗） |
| spatial.ols_regression | OLS+空间诊断 | VALIDATED | ols_regression | spatial_regression.py:637 | ols_regression_analysis **v2**（cov_type） | anselin1988, JB1980, BP1979, moran1950, MacKinnon-White1985 | validation+sig | 系数恢复 1e-10；LM<1e-10；HC<1e-12 | 无 | 6 | 有（平面/LM 手算/JB-BP/HC0/HC3/默认不变） |
| spatial.sar_ml | SAR-ML | VALIDATED | sar_ml_regression | spatial_regression.py:869（Ord1975 特征值+Brent） | sar_ml_analysis v1 | ord1975, anselin1988 | validation+sig | ρ=0.6 恢复 ±0.15 | **有**：eigen_dense_symmetric(numpy, n≤4000) | 2 | 有（格网恢复/scale guard） |
| spatial.sem_ml | SEM-ML | VALIDATED | sem_ml_regression | spatial_regression.py:955（GLS 剖面） | sem_ml_analysis v1 | ord1975, anselin1988 | validation+sig | —（未声明数值容差） | 无 | 2 | 有 |
| spatial.slx | SLX | VALIDATED | slx_regression | spatial_regression.py:772 | slx_analysis v1 | anselin1988, cliff_ord1973 | validation | — | 无 | 1 | 有（滞后项恢复） |
| spatial.gwr | GWR | VALIDATED | gwr_regression | spatial_regression.py:1167（bisquare-kNN 局地 WLS+CV） | gwr_analysis v1 | brunsdon1996, fotheringham2002 | validation + field_uncertainty + **sensitivity_envelope（仅 cv 路径）** | 手算 WLS <5e-7 | 无 | 2 | 有（手算+CV/guards） |
| stats.weights_sensitivity | 权重敏感性 | VALIDATED | weights_sensitivity | statistics.py:2889 | weights_sensitivity_analysis v1 | moran1950, anselin1988 | sensitivity+sig | — | 无 | 1 | 有 |
| spatial.mgwr | MGWR | VALIDATED | mgwr_regression | spatial_regression.py:1540/1454（**真 backfitting**） | mgwr_analysis v1 | fotheringham2017, fotheringham2002, brunsdon1996 | validation+field+sensitivity | 等带宽锚 rtol 1e-4 | 无（未声明 backend_variants） | 3 | 有（等带宽 GWR 锚/异带宽变面/guards/确定性） |
| stats.geodetector_ecological | 生态探测器 | VALIDATED | geodetector_ecological | statistics.py:3056（SSW t 检验，df=n−2 披露） | geodetector_ecological_analysis v1 | wang2010 | sig | 手算黄金值逐位 | 无 | 2 | 有 |
| stats.geodetector_risk | 风险探测器 | VALIDATED | geodetector_risk | statistics.py:3215（Welch t+置换） | geodetector_risk_analysis v1 | wang2010 | sig | 与 scipy.ttest_ind 同实现 | 无 | 2 | 有 |
| stats.local_join_count | 局部 Join Count | VALIDATED | local_join_count | statistics.py:3378（条件置换+焦点族校正） | local_join_count_analysis v1 | anselin_li2019, sokal1998, BH1995 | sig | 整数精确；seed42 | 无 | 3 | 有（手算/非二值/确定性） |
| stats.bivariate_local_moran | 双变量局部 Moran | VALIDATED | bivariate_local_moran | statistics.py:3553（esda.Moran_Local_BV 委托） | bivariate_local_moran_analysis v1 | anselin1995, wartenberg1985, BH1995 | sig | 逐位（委托） | 无 | 1 | 有 |
| stats.weights_diagnostics | 权重诊断 | VALIDATED | weights_diagnostics | statistics.py:3690 | weights_diagnostics_analysis v1 | anselin1988 | —（确定性） | 整数精确 | 无 | 1 | 有 |

结论速览：27 个 descriptor 中 25 个 scientific_status=VALIDATED 且 conformance 节点全部实存实过；契约→工具→实现三方签名 parity 一致（抽查 ols/gwr/mgwr/sar/gi_star 五条）。**MGWR 是真实的逐变量带宽反向拟合**（GWR 热启动 → 逐项部分残差 → LOO-CV 网格选带宽 → Gauss-Seidel 更新；闭式逐项帽迹 ENP；等带宽锚测试以 rtol 1e-4 钉死"不是改名的 GWR"），非 fake。SDM/SAC/quantile LISA 全仓不存在（descriptor 也未虚报）。

## 2. Fake-native / 声明不实 findings

| # | 严重度 | finding |
|---|---|---|
| F-1 | **MEDIUM** | `stats.h3_hotspot` 的 tool_candidates=["h3_lisa"]，但 `statistics.h3_lisa`（statistics.py:1276）只产 LISA 象限标签，**不产 Gi* z 值、逐格 p、q_value_fdr**；Gi*+BH q 的实现在 `hotspot_narrated`（:948，工具 hotspot_analysis）。descriptor 的 assumptions 描述的是后者的行为——声明与工具接线错位，实际等价于"对 H3 网格跑 hotspot_analysis"。 |
| F-2 | **MEDIUM** | `stats.join_count` descriptor assumptions 写"期望/方差用 **free sampling**（Cliff-Ord 1973）解析式"——实现（statistics.py:1927 注释 V3 review M1）已修正为 **non-free sampling**（条件于类别边际）并在 docstring 承认此前误标；descriptor 未同步，且同一输出的证据块标签仍自相矛盾：n_BW 写 "(non-free sampling z-test)"（:2038）、n_BB 写 "(free sampling z-test)"（:2046）。bivariate_join_count descriptor 同病（"端点独立抽取…free sampling"）。 |
| F-3 | **LOW-MED** | `spatial.hotspot.local` / `stats.h3_hotspot` / `stats.h3_lisa` 声明 `uncertainty_outputs=["statistical_significance"]`，但三条路径的 data_out 均无 `uncertainty` 证据块（域内其他 20+ 算法都经 StatisticalSignificance.to_evidence() 落块）；且 `h3_lisa` / `st_dbscan` 工具（spatial_stats.py:719/745）不调 `_attach_scientific_evidence`，科学元数据（seed/CRS/证据）整体缺失。 |
| F-4 | LOW | `spatial.gwr` 无条件声明 `sensitivity_envelope`，实际仅 `bandwidth_selection=cv` 时产出（fixed 路径 envelope=None，spatial_regression.py:1205-1223）；fixed 是契约默认值 → 默认调用与声明不符。 |
| F-5 | LOW | `spatial.sem_ml` / `spatial.slx` 等多个 VALIDATED descriptor 缺 numerical_tolerance 声明（SAR 有、SEM 无），域内声明颗粒度不齐。 |
| F-6 | LOW | OLS narrative 的 `sig_counts` 口径为 p<0.05 计数（:747），但稳健 cov_type 下 narrative 未提示 p 值仍是 classic 口径（robust 列只加不改）——不算造假（有 cov_type_disclosure 键）但叙事易误导。 |
| F-7 | INFO | `spatial.hotspot.local` normal 路径经 `SpatialAnalyzer.hotspot`（services 层薄委托回 `hotspot_narrated`，distance_band 参数不经过 apply_contract 的 E-7 默认描述）——单实现双通道，无假实现，但 maintenance 面大。 |

未发现"planned 冒充 native"或硬编码输出类 fake-native：置换 p、esda 对齐、手算 golden 全部有测试且实跑通过；MGWR 收敛轨迹/ENP/rss_trajectory 均真实计算并对披露（converged=False 会如实输出）。

## 3. 契约缺口

1. **h3_lisa / st_dbscan / profile / category_breakdown 无 ParameterContract**：h3_lisa 工具签名只有 `value_field`——置换数（esda 默认 999?实现用 seed=42 默认 perms）、权重方案（写死 Queen）均不可调、不可披露；st_dbscan 的 eps1/eps2/min_samples/timestamp_field 游离于契约体系外。
2. **GWR 契约缺 cov 诊断口**：无 `adaptive vs fixed kernel`、无 AICc 选带宽选项（只有 LOO-CV）；MGWR 契约未暴露 `max_iterations / tolerance / fixed_bandwidths`（实现已支持，conformance 测试正是靠 fixed_bandwidths 打锚——工具层用户够不到）。
3. **OLS cov_type 缺 HC2**（MacKinnon-White 1985 三件套的漏项）；稳健 SE 未扩展到 SAR/SEM/SLX。
4. **weights_scheme 词表不含 inverse_distance**：`spatial_weights.build_inverse_distance_weights` 已实现（含 n≤2000 诚实上限），但 22 个契约无一暴露，属"实现了没接线"。
5. **单变量局部 Moran 无通用入口**：唯一入口 h3_lisa（名字锁死 H3 心智），多边形/任意面要素的 LISA 需借用它；无 weights/k/permutations 契约参数。
6. **残差诊断不随模型走**：SLX 输出无 JB/BP/残差 Moran/LM（OLS 有）；GWR/MGWR 无残差空间自相关输出。
7. **join_count 契约 descriptions**（"0=只用 free-sampling 解析 z 检验"）与实现的 non-free 口径需同步（同 F-2）。

## 4. 数值风险图

- **低风险**：全局三统计量与 esda 逐式对齐（conformance <1e-10）；置换推断统一 (count+1)/(perms+1) 固定 seed 42、永不返回精确 0；Gi* 解析 p 下溢钳 1e-16；join count 解析方差非正时类型化拒绝/置换兜底（R2 MAJOR-2 修复）；kNN 重合点 tie-break 的 E-4 自环防御两处实现均已修；local_geary 孤岛行 C_i≡0→p=1 数学自洽（期望用行和缩放，正确）。
- **中风险**：
  - **SAR/SEM 的 β 标准误是"给定 ρ̂/λ̂ 的条件渐近近似"**，不含 ρ 估计不确定性（docstring/叙事已披露），t/p 只可作量级参考——下游若当真 p 值用会偏乐观。
  - **OLS 用 `pinv` 求 (X'X)⁻¹**：接近共线时无病态守卫（不抛 IllConditionedSystem），靠 VIF 事后暴露；HC 夹心同样基于 pinv。
  - **GWR 局部 hat 迹用 `np.linalg.inv`**（:1120）——已由邻域点数 ≥p 守卫前置，但条件数差的局部系统仍可能放大舍入（局部解有 non-finite 检查兜底）。
  - **Gi* 置换条件化与严格 y_{−i} 差一项**（邻居多重集含自身一次），已披露为 Monte-Carlo 近似。
  - **auto-UTM 投影**：跨带大数据失真（全部 descriptor 已披露）；knn 对面要素用 centroid，非面邻接近似（已披露）。
- **已披露的近似（非风险但需保知）**：Geary 解析方差依赖正态；MGWR ENP 忽略 backfitting 复合算子交叉项（enp_note）；MGWR 局部 R² 用截距带宽；AICc 无唯一公认式；bh 校正在含 p≡1 位置时偏保守（local_join_count 已改为焦点族校正，local_geary 仍是全 n 族）。

## 5. 规模/后端缺口

| 算法 | 现状 | 缺口 |
|---|---|---|
| SAR/SEM | n>4000 先拒绝（稠密 eigvalsh O(n³)）；唯一 backend_variant `eigen_dense_symmetric` | 无稀疏 log-det（Chebyshev/Lanczos/特征带近似）、无矩估计（2SLS/GMM Kelejian-Prucha）路径；单点故障于 numpy 稠密特征值 |
| GWR | 系数面 n≤2000 截断（超出只回摘要，**但仍全量计算 betas**）；CV=9 候选 × 全量局地拟合（≈10× 单次成本） | 无分块/近似 GWR（如子采样核拟合、随机化 CV）；固定模式不产 envelope（F-4）；O(n·k·p²) 无并行声明 |
| MGWR | n>2000 硬拒绝、p≤20；每扫掠 O(m·\|grid\|·n·k) LOOCV | 无 THREAD 以外的 backend_variants 声明（与其余域不一致）；无收敛加速（Nesterov/步长） |
| 置换族 | 向量化 over nnz，O(perms·nnz) | Gi* 置换 n≤5000 硬顶；h3_lisa 无置换参数（esda 默认 999 硬编码在委托里） |
| 权重 | knn/distance_band 稀疏端到端；inverse_distance O(n²) n≤2000 诚实拒绝 | inverse_distance 无契约暴露（见 §3.4） |
| **spatial block CV** | **域内缺失**——仅 kriging（interpolation 域）有 `_spatial_block_folds`（kriging.py:1005）+ cv_scheme 契约 | 回归/GWR/MGWR 的 CV 全是随机/index LOO，空间依赖下乐观（详见 §8-B3） |

## 6. 不确定性缺口

1. **GWR/MGWR 无逐系数局地标准误/t 值**：不确定度只有系数面 IQR 摘要 + 带宽 envelope。参考实现（pysal mgwr/GWR）提供 `bse/se`（(X'W_iX)⁻¹σ̂² 局部口径）与过滤 t 值——本实现完全缺位，是域内最大不确定性缺口（P0）。
2. SAR/SEM：无 ρ/λ 的不确定度（无 profile likelihood 区间/蒙特卡洛），无空间 HAC 稳健协方差（Kelejian-Prucha 2007）；SE 条件渐近已披露但无替代口径。
3. SLX：无残差 Moran/JB/BP（模型比较与"要不要升级 SAR/SEM"缺证据）。
4. 局部族（local_geary/join_count/bv_local_moran）：有置换 p + 校正 q，但无 z_sim / 标准化统计量输出（esda 有 z_sim 未透传）；h3_lisa 连 BH q 都不输出（仅期望假阳性披露）。
5. 风险探测器多对比较未校正（已披露）——分层数大时族错误失控；生态探测器 df=n−2 为近似（已披露，可升级 Welch–Satterthwaite）。
6. rate_smoothing `uncertainty_outputs=[]`：收缩权重 w_i 本身是天然的不确定度画像（有效样本量/收缩幅度），未随结果输出。

## 7. 参考文献验证

statistics 域引用的 22 个 method_references id（moran1950, geary1954, getis_ord1992, ord_getis1995, anselin1995, benjamini_hochberg1995, ester_kriegel1996, cliff_ord1973, wartenberg1985, wang2010, anselin1988, ord1975, brunsdon1996, fotheringham2002, jarque_bera1980, breusch_pagan1979, holm1979, fotheringham2017, anselin_li2019, sokal1998, mackinnon_white1985, marshall1991）**全部在 `app/lib/gis/method_references.py` 实存**，registry validate 有悬空引用校验。两处归因瑕疵：
- `_breusch_pagan`（spatial_regression.py:338）实现的是 **Koenker 学生化 BP**（n·R² 对 e²/ē），descriptor references 只有 breusch_pagan1979——宜补 koenker1981（assumptions 文字已如实写 Koenker，仅引用目录缺条目）。
- HC0 归 White 1980、LM-error 归 Burridge 1980：代码注释已写，method_references 目录无 white1980/burridge1980 条目（被 mackinnon_white1985/anselin1988 覆盖，可接受但可补）。
- anselin_li2019（局部 join count）、fotheringham2017（MGWR）、marshall1991（EB MOM）、wartenberg1985（双变量 Moran）引用-实现对应关系逐条核实无误。

## 8. V3 建议清单

依赖基线：libpysal/esda/networkx/scipy 可用；**statsmodels / mgwr / spreg 缺失**（SAR/SEM/GWR/MGWR 均为手写，这是既有事实也是继续手写增强的约束）。位置统一指 `app/lib/geo_analysis/spatial_regression.py`（回归侧）与 `statistics.py`（自相关侧），工具 `app/tools/spatial_stats.py`，契约 `algorithms/statistics.py`。

| # | 项 | 方法引用 | 现状差距 | 可行性（现有依赖） | 实现位置 | 测试策略 | 优先级 |
|---|---|---|---|---|---|---|---|
| A1 | **GWR/MGWR 逐系数局地 SE + t 值** | Fotheringham 2002 §2.6；pysal mgwr 的 bse 口径 | 完全缺位（§6.1） | 高：终拟合循环内已有 X'W_iX，补 σ̂² 与 (X'W_iX)⁻¹ 对角即得；MGWR 用 backfitting 残差近似 σ̂²（披露口径） | `_gwr_local_fit` 返回值扩展 + `gwr/mgwr_regression_narrated` 输出 `coefficient_se_stats` + surfaces 可选 | 精确平面 + 常数方差 → 局地 SE≈OLS SE（rtol 1e-6）；异方差植入 → SE 空间变异检出 | **P0** |
| A2 | **h3_hotspot/h3_lisa 接线与契约修复**（F-1/F-3） | — | tool_candidates 错位、证据块缺失、无置换参数 | 高：descriptor tool_candidates 改 hotspot_analysis（或给 h3_lisa 加 Gi* 分支）；工具层补 `_attach_scientific_evidence` + `permutations/weights` 契约 | algorithms/statistics.py（descriptor）、tools/spatial_stats.py:702 | 既有 test_statistics_vector 锚不动；新增契约 parity 测试 | **P0** |
| A3 | **join_count 口径同步**（F-2） | Cliff-Ord 1973 non-free | descriptor 假设 + 证据块标签残留 free sampling | 高：纯文案/标签改 | descriptor :376；statistics.py:2038-2050 | 既有 golden 测试不改即绿（数值不变） | **P0** |
| B1 | **MGWR 增强**：AICc 选带宽选项、连续带宽（黄金分割）、收敛加速 | Fotheringham-Yang-Kang 2017；pysal mgwr | 只有逐项 LOO-CV 有界网格（≤20 点）；已披露非连续优化 | 高：`_uni_term_loocv` 已可对任意 k 求值，黄金分割只需包一层；AICc 路径用现有 ENP 机器 | `_mgwr_backfitting` 加 `selection="cv"\|"aicc"`；契约加枚举 | 网格 vs 黄金分割在单峰 CV 曲线 fixture 上选带宽一致（±1 格）；aicc 选带宽 ≤ cv 的 RSS 单调性 | **P1** |
| B2 | **local collinearity diagnostics** | Fotheringham 2002（local condition number）；Anselin 2019 综述 | 仅 limitations 文字披露"全局 VIF 不代表局部"，无任何局部共线量化 | 高：纯 numpy——逐位置 cond(X'W_iX) 或局部 VIF；复用近邻表 | spatial_regression.py 新 `_gwr_local_collinearity`；GWR/MGWR 输出 `local_collinearity` 摘要 + 阈值旗标 | 植入两列近共线 → 逐位置 cond 数值断层 vs 正交对照；阈值旗标一致性 | **P1** |
| B3 | **spatial block CV**（GWR 带宽选择 + OLS 验证） | Roberts et al. 2017（block CV）; kriging 内部已有折法 | 域内全为 LOO/index CV，空间自相关下乐观（§5） | 高：`kriging._spatial_block_folds` 提为共享工具（geo_analysis/_vector 或新模块），GWR cv_scheme="spatial_block" 用块级 LOO 重算 cv_mse | spatial_regression.py + 契约（照抄 kriging 的 index/spatial_block 枚举） | 聚集合成数据：block CV RMSE > LOO RMSE 断言；折分配确定性 | **P1** |
| B4 | **SDM**（SAR+WX） | LeSage-Pace 2009 | 无；descriptor 诚实未列（SLX limitations 提"需 SAR/SDM 才有分解"） | 高：`_ml_lag_fit` 直接作用于 [X, WX] 设计阵即可；LR vs SAR/SLX 现成 | spatial_regression.py `sdm_regression_narrated` | ρ 恢复复用 SAR 测试模板；WX 系数恢复（y=WXβ 真值） | **P1** |
| B5 | **SLX/OLS 诊断对齐**：SLX 补残差 Moran/JB/BP/LM | Anselin 1988 | SLX 只有 R²/VIF | 高：`_lm_spatial_diagnostics/_residual_morans_i/_jarque_bera` 直接复用 | slx_regression_narrated | 既有 OLS 诊断测试模板移植 | **P1** |
| B6 | **hotspot 时间演化（emerging hot spots）** | Harris et al. 2017（ESHA 分类） | 完全缺位；时间维度只有 st_dbscan | 中-高：逐期 Gi*（现有 hotspot_narrated）+ Mann-Kendall 趋势（scipy.kendalltau）→ 分类 new/intensifying/persistent/diminishing/oscillating/sporadic；需多期输入聚合器 | statistics.py 新函数 + temporal 域协作；工具在 spatial_stats | 合成时间序列（单调升/降/震荡）→ 分类金标；seed42 确定性 | **P1** |
| B7 | **局部 Geary / join count 扩展**：多变量局部 Geary、多色（k>2）局部 join count、局部 Moran 条件化校验 | Anselin 2019；Anselin-Li 2019 | local_geary 仅单变量；local_join_count 仅二值 | 高：多变量局部 Geary=Σ_m C_i,m 求和（esda 无，手写 30 行，复用 bincount 路径）；多色 LJC=逐类别 one-vs-rest + 焦点族 BH | statistics.py + 契约 | checkerboard/三色手算 fixture；与单变量路径在 k=2 时一致 | **P2** |
| B8 | **bivariate local Moran 已落地，补 z_sim 透传** | esda.Moran_Local_BV | p_sim/q 有，z_sim 未透传 | 高：一行属性透传 | bivariate_local_moran_narrated | 与 p_sim 单调一致断言 | **P2** |
| B9 | **quantile LISA** | Kuan 2019（rank-based LISA） | 无 | 中：esda 无现成；务实路径 = 秩变换 + Moran_Local + 披露（非严格 Kuan 定义则命名 rank_lisa） | statistics.py | 与 Moran_Local 在线性变换值上标签一致性（秩变换不变性） | **P2** |
| B10 | **SAC**（SARAR）与 SAR/SEM 大 n 矩估计 | Anselin 1988；Kelejian-Prucha 1998/2007 | 无 | 中：SAC 双参数需二维有界搜索（嵌套 Brent 可行但慢）；2SLS/GMM 免特征值可破 4000 顶 | spatial_regression.py | ρ/λ 网格恢复；2SLS 与 ML 在正态小样本一致性（宽松 rtol） | **P2** |
| B11 | **空间 HAC 稳健 SE**（SAR/SEM/SLX）+ OLS HC2 | Kelejian-Prucha 2007；MacKinnon-White 1985 | 只有 OLS HC0/1/3 | 高（HC2 一行：e²/(1−h)）；HAC 中 | `_hc_covariance` 加 HC2；SAR/SEM 加 KP 选项 | 闭式夹心手算 fixture（照 HC0/HC3 测试） | **P2** |
| B12 | **模型比较表工具**（同权重下 OLS/SAR/SEM/SLX(/SDM) AIC/logL/LR 汇总） | Burnham-Anderson | 各模型分奏，比较靠用户手拼 | 高：纯组装 | tools/spatial_stats.py 新 tool | 输入不变 → 汇总与单模型输出逐位一致 | **P2** |
| B13 | **空间分层异质性扩展**：风险探测对间 BH 校正选项、生态探测 Welch–Satterthwaite df、最优分箱（max-q 搜索） | Wang 2010 及后续 | 两处近似/缺口已披露（§6.5-6） | 高：BH 复用 multiple_testing_correction；W-S df 是公式改写；max-q 网格搜索复用 _geodetector_q | statistics.py geodetector 族 | 已有手算 fixture 加 df 对照；BH 后族错误率 Monte-Carlo 断言 | **P1** |

## 9. 现有算法增强建议

1. **OLS**：加 HC2（一行）；(X'X) 条件数守卫（>1/ε 时 IllConditionedSystem 或最低限披露）；可选 White 检验；robust 路径 narrative 注明 p 值口径（F-6）。
2. **全局 Moran/Geary**：输出置换 z / 期望 alongside p（换算现有 perm_stats 即可）；契约允许 999/499 为高阶档已有，默认档考虑升 199。
3. **Gi***：normal 路径收敛到 `hotspot_narrated` 单通道（消灭 SpatialAnalyzer 双通道，F-7）；permutation 路径补 uncertainty 证据块（F-3）。
4. **h3_lisa**：暴露 permutations/weights 契约参数；补 BH q 值与 uncertainty 块；输出 lisa Is 值（当前只有标签与计数）。
5. **join_count**：统一 non-free 标签（F-2）；小 n（J≤200）加精确条件枚举选项。
6. **rate_smoothing**：输出逐区收缩权重 w_i 与有效样本量；可选 Poisson-Gamma 全 EB（CLG 2005）对照口径。
7. **st_dbscan**：加 k-dist 图辅助 ε 选择的姊妹工具；minPts/ε 自动扫描（披露式网格）。
8. **weights_sensitivity**：方案集加 Geary/General G 交叉验证视角；输出逐方案孤岛数（metadata 已有，进叙事）。
9. **geodetector**：交互探测器扩展到 >2 因子两两矩阵；q 置换分布加完整分位数输出。
10. **bivariate_moran**：含孤岛时改用 S0 口径修正与 esda 严格对齐（limitations 已披露发散来源，属可修项）。
