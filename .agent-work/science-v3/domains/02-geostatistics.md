# 02 Geostatistics 审计

- 审计对象：worktree `/home/kevin/projects/webgis/webgis-ai-agent-science-v3`（分支 `feat/spatial-science-geoai-platform-v3`，HEAD 16d1c70）
- 审计日期：2026-09-07（只读审计；唯一写入本文件）
- 域事实源：`app/lib/gis/algorithms/interpolation.py`（14 个 descriptor + 11 个域包契约）；中央契约 `app/lib/gis/parameter_contracts.py`（idw_interpolation v1、kriging_interpolation v3）
- 核心实现：`app/lib/geo_analysis/{interpolation,kriging,rbf_interpolation,tin_interpolation,trend_surface,regression_kriging,interpolation_compare}.py`（共 ≈5,600 行库级实现）
- 工具层：`app/tools/advanced_spatial.py`（14 个工具全部注册：idw_interpolation / kriging_interpolation / rbf_interpolation / tin_interpolation / trend_surface / regression_kriging / interpolation_model_compare / directional_variogram_analysis / variogram_model_selection / indicator_kriging_surface / cokriging_surface / nearest_neighbor_surface / natural_neighbor_surface / block_kriging_surface）
- 动态核验：`registry.validate()` 0 issues；59/59 conformance 节点 AST 存在；插值域 7 个测试文件 + idw + vertical slice 共 **134 个测试全部通过**（pytest 实跑）；science_oracles `geostat.json` 含 253 个独立复算 oracle（γ 模型闭式、anisotropy 变换、IDW LOOCV、OK、驱动级等）
- 依赖事实：numpy 2.4.6 / scipy 1.17.1（curve_fit、cKDTree、LU、special.kv、RBFInterpolator、Delaunay、lstsq）；pykrige / gstools / statsmodels 确认缺失 → 地统计为 **纯 numpy/scipy native 实现**（属实，非 stub）

---

## 1. Census 表

| id | name | status (runtime/sci) | tools | 实现位置 | 契约 | references | uncertainty | tolerance | variants (窗口) | conformance | 测试覆盖 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| interpolation.idw | IDW 插值 | native / VALIDATED | idw_interpolation | geo_analysis/interpolation.py（idw_surface + _idw_loocv_residuals） | idw_interpolation v1（中央） | shepard1968 | validation_metrics（仅 LOOCV 残差，**无方差冒充**） | 向量化解 <1e-6；精确命中 1e-9 m | 无 | 6 节点 | test_idw_interpolation(16)+vnext(6)+oracle idw_loocv×15/idw_driver×2 |
| interpolation.kriging | 普通克里金 | native / **PRODUCTION** | kriging_interpolation | geo_analysis/kriging.py（fit_variogram + ordinary_kriging + cross_validate_kriging + driver） | kriging_interpolation v3（中央，method=ordinary） | matheron1963 | raster_uncertainty + validation_metrics（真克里金方差面） | chunk 1024；ridge 1e-6·sill（gaussian 1e-2·sill）；钳制 ±3√sill | numpy_batched / scipy_linalg（**窗口均未声明**） | 6 节点 | test_kriging_interpolation(30)+vnext(3)+vertical_slice(1)+oracle kriging_driver×17 |
| interpolation.rbf | RBF 径向基 | native / VALIDATED | rbf_interpolation | geo_analysis/rbf_interpolation.py（scipy RBFInterpolator 包装 + LOOCV） | rbf_interpolation v1（域包） | **[]（缺口）** | validation_metrics | smoothing=0 节点精确 <1e-9 | 无 | 5 节点 | vnext(6)+oracle rbf_exact×5/rbf_loocv×2 |
| interpolation.universal_kriging | 泛克里金 | native / VALIDATED | kriging_interpolation (method=universal) | kriging.py（ols_linear_trend + universal_kriging + universal_kriging_detrended） | kriging_interpolation v3（method 枚举） | matheron1963 | raster_uncertainty + validation_metrics（UK 方差 = wᵗγ0+mᵗf0） | 同 OK；零残差退化方差精确 0 | 无 | 3 节点 | vnext(UK 3 个)+oracle — |
| interpolation.tin | TIN 三角网 | native / VALIDATED | tin_interpolation | geo_analysis/tin_interpolation.py（Delaunay linear/clough_tocher） | tin_interpolation v1（域包） | watson1981, clough_tocher1966 | validation_metrics | linear 样本格心精确 <1e-9 | 无 | 6 节点 | test_tin_interpolation(14) |
| interpolation.trend_surface | 趋势面 | native / VALIDATED | trend_surface | geo_analysis/trend_surface.py（单位盒缩放 OLS 多项式） | trend_surface_analysis v1（域包） | webster_oliver2007 | validation_metrics（残差方差证据） | 平面场恢复 ≤1e-9·量级 | 无 | 5 节点 | test_trend_surface(9) |
| interpolation.regression_kriging | 回归克里金 | native / VALIDATED, **approximate=True** | regression_kriging | geo_analysis/regression_kriging.py（OLS 趋势 + 残差 OK + 目标协变量 IDW 近似） | regression_kriging_analysis v1（域包） | odeh1995, matheron1963 | raster_uncertainty + validation_metrics（**rk_variance=残差克里金方差 only，已披露**） | 平面协变量精确恢复；LOOCV 有界 200 | 无 | 6 节点 | test_regression_kriging(8) |
| interpolation.model_compare | 插值模型比较 | native / VALIDATED | interpolation_model_compare | geo_analysis/interpolation_compare.py（固定序 + cv_budget 走查） | interpolation_model_compare v1（域包） | shepard1968, matheron1963 | validation_metrics | 同输入逐字节一致；与方法库级逐位一致 | 无 | 5 节点 | test_interpolation_compare(6) |
| interpolation.directional_variogram | 方向变异函数 | native / VALIDATED | directional_variogram_analysis | kriging.py directional_variogram | directional_variogram_analysis v1（域包） | webster_oliver2007, isaaks_srivastava1989 | []（诊断表） | tolerance=90° 与全向逐位一致 | 无 | 3 节点 | test_geostat_v3(3) |
| interpolation.variogram_selection | 变异函数模型选择 | native / VALIDATED | variogram_model_selection | kriging.py select_variogram_model（6 家族 + AICc） | variogram_selection_analysis v1（域包） | webster_oliver2007, matern1986 | []（诊断表） | 同输入排名逐字节一致 | 无 | 2 节点 | test_geostat_v3(2) |
| interpolation.indicator_kriging | 指示克里金 | native / VALIDATED | indicator_kriging_surface | kriging.py indicator_kriging + surface driver | indicator_kriging_analysis v1（域包） | journel1983, matheron1963 | raster_uncertainty（**声明但工具未产出 typed 块，见 F2**） | 概率面 ∈[0,1]（钳制计数）；同输入逐位一致 | 无 | 3 节点 | test_geostat_v3(3)+oracle indicator_kriging×5 |
| interpolation.cokriging | 协同克里金 | native / VALIDATED, **approximate=True** (MM1) | cokriging_surface | kriging.py collocated_cokriging + driver | cokriging_analysis v1（域包） | journel_huijbregts1978, matheron1963 | raster_uncertainty（ck_variance 面） | 同 OK 稳定化；方差钳 ≥0 计数 | 无 | 2 节点 | test_geostat_v3(3) |
| interpolation.nearest_neighbor | 最近邻 | native / VALIDATED | nearest_neighbor_surface | interpolation.py nearest_neighbor_interpolation（cKDTree k=1） | nearest_neighbor_analysis v1（域包） | [] | []（诚实：无方差无 CV） | 每格值精确=最近样本 | 无 | 2 节点 | test_geostat_v3(2) |
| interpolation.natural_neighbor | 自然邻域 | native / VALIDATED | natural_neighbor_surface | tin_interpolation.py natural_neighbor_interpolation（Sibson 面积裁剪 + Watson walk） | natural_neighbor_analysis v1（域包） | sibson1981, watson1981 | validation_metrics | 样本 float64 精确；平面复现 ≤1e-6 | 无 | 3 节点 | test_geostat_v3(3) |

汇总：14/14 native；1 PRODUCTION + 13 VALIDATED；59/59 conformance 节点实存；实现深度全部为真算法（无 stub、无 mock 路径；UK 零残差退化是**诚实地不拟合**而非造假）。

---

## 2. Fake-native / 科学不诚实 findings

**结论先行：本域未发现 fake-native。** kriging 家族为真克里金：经验半变异函数 binning（行步幅 pair 预算）→ 有界加权 LS 拟合（curve_fit + 网格回退，bounds 防退化模型）→ k 邻域 (k+1)² 系统 np.linalg.solve/LU 批式求解（含 Lagrange 乘子）→ 真克里金方差（wᵗγ0+μ；UK 为 wᵗγ0+mᵗf0；block 为 −γ̄(B,B) 修正；CoK 为协方差形式）。审计重点逐条核验：

1. **IDW 不确定性诚实（通过）**。descriptor 只声明 `uncertainty_outputs=["validation_metrics"]`；`idw_surface` 输出 LOOCV rmse/mae/bias + 绝对残差 p50/p90 分位数，note 逐字写明"IDW 无理论方差——不确定性以 LOOCV 绝对残差的经验分位数表达"。**没有**用 IDW 误差冒充 kriging variance。
2. **RK 方差语义诚实（通过）**。`rk_variance` 仅残差克里金方差；趋势系数不确定性未传播在 descriptor assumptions、metadata disclosures、`uncertainty.method="residual_kriging_variance_plus_loocv"` 三处一致披露；有专项测试 `test_rk_variance_is_residual_kriging_variance_only`。
3. **退化情形不造假（通过）**。UK/RK 零残差退化时方差精确为 0、不制造假变异函数、disclosure flag `zero_residual_variance`；indicator 常量指示场不拟合变异函数、常量概率披露。

发现的问题（按严重度）：

- **[F1 · MEDIUM · 功能性守卫 bug] indicator_kriging 工具阈值上限检查对象错误**
  `app/tools/advanced_spatial.py`（indicator_kriging_surface 内，约 L1352）：`if len(thresholds) > 20:` 检查的是**原始字符串**（如 `"35,75,115"`）的字符数而非 `thr_list` 的阈值个数。后果：8 个阈值的合法请求 `"10,20,30,40,50,60,70,80"`（23 字符）被误拒，且错误消息谎报"需要 23 次独立拟合"。应改为 `len(thr_list) > 20`。科学上属守卫失真（过严+消息失实），非算法错误。
- **[F2 · MEDIUM-LOW] indicator_kriging 声明 `raster_uncertainty` 但工具不产出 typed 不确定产物**
  descriptor `uncertainty_outputs=["raster_uncertainty"]`，概率面确为不确定性产物，但工具结果只有逐要素 `p_le_*` properties + probability_summary，**没有**像 OK/UK/RK/CoK/BK 那样输出 RasterUncertainty 证据块或独立 uncertainty FC。registry 只做词表成员校验、无法校验"声明即产出"。建议补 typed 块（p_max 或 p50 的面摘要）或把词表改为 validation_metrics。
- **[F3 · MINOR] `interpolation.idw` → kriging 的 fallback_semantics="equivalent" 过强**
  kriging 带 nugget 时非精确插值器（IDW 精确过样本点）；且 kriging `min_features=8` vs IDW 实际 ≥1——1~7 样本时 fallback 硬失败（InsufficientSamples）；CRS 语义亦不同（GEOGRAPHIC_OK 自动投影 vs PROJECTED_REQUIRED 结构化拒绝不支持 CRS）。三方面都不满足"equivalent"，应降为 `approximation` 或在 fallback 链上表达适用窗口。
- **[F4 · MINOR] `interpolation.rbf`（VALIDATED）与 nearest_neighbor 的 `method_references=[]`**
  RBF/薄板样条应引 Duchon (1977) / Buhmann (2003)（或 Hardy 1971 多二次）；NN 可引 Thiessen/Voronoi 惯例。registry 校验只对 PRODUCTION 强制出处，VALIDATED 漏网。
- **[F5 · MINOR] kriging backend_variants 无规模窗口 → scipy_linalg 变体在选择层死亡**
  两变体 `min_features/max_features` 均为 None，`select_backend` 按声明序恒选 numpy_batched；工具层 `elif decision.variant_id == "scipy_linalg"` 为 dead 分支，scipy_linalg 仅显式 `solve_backend` 参数可达。metadata 存在但不起选择作用（与其"不再是纯 metadata"的设计声明不符）。
- **[F6 · MINOR] 14 个 descriptor 的 `complexity` 字段全空、`max_features_hint` 未声明**
  规模守卫全部藏在实现常量里（见 §5）。对 planner/cost_model 而言 cpu_cost=high 是唯一信号；复杂度声明（如 kriging 预测 O(n_t·k³)、拟合 O(N_fit²)）应上收到 descriptor。

---

## 3. 契约缺口

1. **kriging_interpolation v3 的 `matern_smoothness` 是契约死参数**：中央契约 v3 声明了该参数（default 0.5），但 (a) 工具签名不含 `matern_smoothness`；(b) 驱动 `variogram_model` 词表被生产一致性套件钉死为 auto/spherical/exponential/gaussian，`matern` 分支 `if variogram_model == "matern"` 经工具路径不可达。matern 平滑度仅库级 `fit_variogram(matern_smoothness=…)` 与 variogram_model_selection 工具真实可用。要么给工具补参数 + 词表扩 matern，要么从 kriging 契约移除该 spec（现状态是"声明了但不可行使"）。
2. **kriging 工具不走 `apply_contract`**：rbf/tin/trend/rk/compare/directional/indicator/cokriging/block 工具全部经 `apply_contract` 校验入参，唯 kriging_interpolation（及 idw）直接传参——契约对这两个工具只有签名 parity 门约束，min/max/default 实际不生效（例如契约 resolution 5–9，实现允许 0–15，用户可传 resolution=12 成功）。
3. **idw 契约 `power` type=integer, min=1 vs 实现 float ∈ (0,5]**：分数幂（如 1.5）与 0<p<1 在库级合法、契约/工具不可达。contract-implementation 漂移（实现更宽）。
4. **resolution 词表漂移**：idw 契约 6–9 / kriging 契约 5–9 vs 实现 `_validate_resolution` 0–15。契约窄于实现是安全方向，但未在任何地方记录该差异；NN 契约 6–9、其余 5–9 同理。
5. **UK 无独立 conformance 覆盖 oracle**：UK 有 3 个单测节点但 `geostat.json` oracle 无 UK 用例（ordinary_kriging×1、kriging_driver×17 覆盖 OK；UK 系统矩阵无独立复算锚点）。
6. **indicator_kriging_analysis 契约 `variogram_model` 枚举（auto/3 族）与 auto 实际行为（逐阈值 6 家族选型）不一致**：契约枚举里没有 matern/wave/cubic，但 `auto` 语义会选中它们；文档已披露但契约枚举与行为语义仍不对称。
7. **model_compare 样本下限（idw≥2/tin≥4/trend≥6/rbf≥3/kriging≥20）只在 assumptions 文本中**，契约无对应表达（小）。
8. **fallback 链适用窗口缺失**：所有 fallback_semantics 只表达等价类，不表达 min_features/CRS 差异（与 F3 同根）。

---

## 4. 数值风险图

| 风险点 | 现状 | 评级 |
|---|---|---|
| **kriging 矩阵奇异** | 精确重复坐标被 `_aggregate_duplicates` 均值聚合（消奇异源）；对角 ridge 1e-6·sill，gaussian/matern(ν≥2) 加强至 1e-2·sill；批式 solve 失败→逐行 LAPACK→邻域均值+局部方差兜底，全部计入 `degraded_cells`（绝不静默）。k≤24 小系统进一步降险 | 低 |
| **nugget 处理** | 规范 I&S 构造：nugget 进所有 h>0 项与 γ0、对角为零（注释明确记录了"对角加 nugget"错误草稿及其数值证据 RMSE 4.95→0.56）；有 `test_ok_survives_nugget_dominated_data`。nugget>0 时精确插值自然失效（正确行为） | 低 |
| **gaussian 短滞后病态** | ridge + 预测钳制 ±3√sill + `test_ok_gaussian_oscillation_is_bounded` | 低 |
| **负克里金方差** | 钳 ≥0 且计数（不隐藏求解失败） | 低 |
| **对数/偏态变换缺失** | 无 log/normal-score 变换。偏态场（污染物类）原尺度克里金可产生负预测与失真方差；当前仅 ±3√sill 钳制兜底，**偏态风险未披露** | **中**（建议 §8-10） |
| **O(n³) 全局系统** | 不存在——k 邻域 (k+1)² 批式求解，k≤24、chunk 1024；变异函数拟合先 `stratified_subsample` 到 ≤2000 点，pair 预算 200k 行步幅控制，峰值内存 O(N) | 低 |
| **empirical_variogram 直接大 n 调用** | 内部调用方均先抽稀到 2000；但该函数为公开 API，若直接喂 500k 点则 stride≈625k → **只剩 1 行配对**，估计退化（数学无错但统计无效）。directional_variogram **没有**预抽稀：大 n 时同样退化为极少行配对（meta 有 n_pairs_kept/total 披露可察觉） | **中低**（建议入口统一预抽稀） |
| **wave 模型超 sill** | hole-effect 设计使然（π~2π 区间超 sill），docstring 明示不钳制 | 低（已披露） |
| **cubic 模型系数** | GSLIB 金系数 7x²−8.75x³+3.5x⁵−0.75x⁷，代码注释明确纠正了任务简报里的降幂错误写法 | 低 |
| **AICc 近似性** | 基于加权残差而非严格 MLE；n ≤ k+2 时诚实取 inf；meta 逐字披露 | 低（已披露） |
| **UK/RK 零残差退化** | 1e-9·量级阈值判定，方差精确 0 + disclosure | 低 |
| **投影/CRS** | 度数强制重投影 UTM/极方位；3857 尺度畸变、跨带 UTM 失真、antimeridian 分裂 bbox 均在 limitations/实现中处理（kriging driver 与 IDW 对齐） | 低（已披露） |
| **块克里金 2×2 离散化** | 块尺寸→0 收敛到点克里金（rtol 1e-3 conformance）；块/变程比大时近似误差增大（已披露，未提供更高密度离散化） | 中低 |
| **CoK MM1 近似** | 交叉结构=ρ·C_pp(h)、次变量仅目标协同定位、C_ss(0)=主先验方差；|ρ|<0.2 类型化拒绝；四条 disclosures 逐字进 meta | 低（近似语义如实标注 approximate=True） |

---

## 5. 规模/后端缺口

- **守卫清单（实现层，均实存且被测试）**：输入 ≤500k（kriging/RK）；拟合样本 ≤2000；pair 预算 200k；邻域 k≤24；求解 chunk 1024；H3 目标格 ≤1.5M（含 pre-polyfill 估算 + 建议降级分辨率）；NN/Sibson 200k 样本 / 4M 格点（ResourceScaleMismatch 类型化拒绝）；TIN >200k 拒绝；RK LOOCV ≤200 点、RBF/TIN/trend LOOCV ≤500 点（确定性 stride 抽稀并披露实际样本数）。
- **缺 approximate/streaming kriging 变体**：1.5M 目标格 × k=24 的 CELERY heavy 任务无 coarse-to-fine / tapering / 自适应邻域的近似变体声明；`backend_variants` 只有求解后端两枚且无窗口（F5）。建议：声明一个 `approximate` 变体（如 k 随密度自适应、多分辨率粗化-细化），并给两枚现有变体补 `min_features/max_features` 窗口让 `select_backend` 真正可判别。
- **directional_variogram / variogram_selection 无输入规模上限**（依赖 pair-stride；见 §4 第 7 行）；建议入口统一 `stratified_subsample` 预抽稀并在 meta 披露。
- **pair budget 语义**：MAX_PAIRS=200k 通过行步幅（系统性隔行）实现——γ 估计无偏（每对等概率入选）但方差增大；对方向变异函数在容差过滤后有效对数可能骤降（meta 已披露 n_pairs_kept）。可考虑分块并行 + 确定性合并以在同等预算下提高有效对数。
- **无分块落盘/流式输出**：1.5M records 走 `ref_offload` 结果通道；求解内存有界，故 streaming 非急需，但 indicator（阈值数 × 概率列）记录体积线性放大，20 阈值上限合理（当前实现受 F1 bug 影响失真）。

## 6. 不确定性缺口

1. **确定性方法无逐格不确定性**（IDW/RBF/TIN/trend）：只有全局 LOOCV 标量 + 残差分位数。官方立场（不伪造方差）正确；可补**邻域稀疏度面**（最近样本距离 / 局部样本密度）作为 honest 的"信息量"指标面（非方差、不冒充），并披露为 sampling-density surface。
2. **无 prediction interval 面**：kriging stddev 已有一等产物，±1.96σ 区间面是零成本增量（UncertaintyMeasure 已支持 prediction_interval）。
3. **无 uncertainty 校准证据**：LOOCV 残差 vs 克里金 σ 的 z-score 标准化误差（Neff/covergage）可量化"方差是否可信"——模型比较框架的高价值指标（见 §9）。
4. **RK 总不确定性**：趋势系数不确定性未传播（已披露）；升级路径 = KED/UK-with-external-drift 或贝叶斯趋势（§8-12）。
5. **indicator 概率面单调性不保证**（已披露）；可加序约束后处理（monotone rearrangement）；typed raster_uncertainty 块缺失（F2）。
6. **变异函数参数无拟合不确定度**（sill/range/nugget 无 CI）；AICc 仅排名证据。profile-RSS 曲线或参数 bootstrap 可补（bootstrap 需引入 caller_seeded 策略，注意与 deterministic 声明的冲突处理）。
7. **CV 残差无逐样本落位输出**：per_fold 已有（spatial_block 含 block_ids）；逐样本残差表建议走 ref 通道供诊断。
8. **无条件模拟缺位**：单次克里金是平滑均值场，方差不等于空间变率；不确定性全分布需 SGS（§8-8）。

## 7. 参考文献验证

对 `app/lib/gis/method_references.py` 中插值域全部 11 个引用逐条核对（id / 主题 / 书目）：

| ref id | 书目 | 判定 |
|---|---|---|
| matheron1963 | Matheron, G. (1963). Principles of Geostatistics. Economic Geology 58(8), 1246–1266 | 正确 |
| shepard1968 | Shepard, D. (1968). ACM-1968, 517–524 | 正确 |
| isaaks_srivastava1989 | An Introduction to Applied Geostatistics. OUP | 正确（块克里金/规范 γ 构造惯例出处恰当） |
| webster_oliver2007 | Geostatistics for Environmental Scientists (2nd ed.). Wiley | 正确（变异函数拟合/各向异性实践） |
| matern1986 | Spatial Variation (2nd ed.), LNMS 36, Springer | 正确 |
| odeh1995 | Geoderma 67(3-4), 215–226（Regression-Kriging） | 正确 |
| journel1983 | Nonparametric Estimation of Spatial Distributions. Math. Geology 15(3), 445–468 | 正确（指示克里金） |
| journel_huijbregts1978 | Mining Geostatistics. Academic Press | 基本正确；**建议加注**：collocated/MM1 的现代表述通常引 Almeida & Journel (1994)；J&H 1978 是教材源 |
| sibson1981 | A Brief Description of Natural Neighbor Interpolation. Wiley, 21–36 | 正确 |
| watson1981 | The Computer Journal 24(2), 167–172 | 正确 |
| clough_tocher1966 | Proc. 1st Conf. Matrix Methods in Structural Mechanics, 515–545 | 正确 |

缺口：RBF 核（薄板样条）无引用（建议补 duchon1977 或 buhmann2003）；UK 残差变异函数实践可加注 Isaaks & Srivastava ch.12（代码注释已提及，但 method_references 未列）。

## 8. V3 建议清单

| # | 项 | 方法引用 | 可行性 | 实现位置 | 测试策略 | 优先级 |
|---|---|---|---|---|---|---|
| 1 | **OK/UK 增强：prediction interval 面（±z·σ）+ LOOCV z-score 校准指标** | Isaaks & Srivastava 1989 | 高——stddev 已有一等产物，纯增量输出 | kriging.py driver + advanced_spatial kriging 工具 | conformance：区间覆盖率在标定高斯场 ≈95%（容差带）；oracle 加 UK 系统独立复算锚 | **P1** |
| 2 | **各向异性自动拟合（directional scan → 几何椭圆）**：多方位角 directional_variogram 扫描拟合 angle/ratio，喂给 OK/UK | Webster & Oliver 2007 | 高——directional_variogram 与 anisotropy_transform 均已存在，缺的是拟合计 | kriging.py（新 `fit_anisotropy`） | 合成各向异性场判别（已知 angle/ratio 恢复 rtol）；各向同性场不误报 | **P1** |
| 3 | **修复 F1 thresholds 守卫 + F2 indicator typed uncertainty 块** | — | 高 | advanced_spatial.py indicator 工具 | thresholds=8 个合法值不再误拒；RasterUncertainty 块出现且值域 [0,1] | **P1（bugfix）** |
| 4 | **robust variogram 估计器（Cressie–Hawkins / Dowd）** opt-in | Cressie & Hawkins 1980; Dowd 1984 | 高——pair walk 已有，估计器是逐 bin 归约替换 | kriging.py empirical_variogram(robust=…) | 污染样本注入下 RMSE 优于 classical；clean 数据两者一致 | P2 |
| 5 | **log / normal-score 变换 + 回变换**（对数正态克里金） | Webster & Oliver 2007 ch.4 | 中高——注意回变换偏差（lognormal 正确回变换含 ½σ² 项） | kriging.py driver（transform 参数） | 合成 lognormal 场负预测率→0、回变换无偏；偏态披露块 | P2 |
| 6 | **KED（外部漂移克里金）**：目标处协变量已知（栅格通道）时的严格 RK | Matheron 1963; Hengl 2007 综述 | 中——需 raster 采样通道；UK 系统已支持任意漂移基 | kriging.py（drift 基替换 [1,x,y]→[1,c1,..]） | 平面协变量恢复=UK 锚；与 RK 对比 LOOCV 改善 | P2 |
| 7 | **spatial block CV 推广到确定性方法 + RK**（当前仅 kriging CV 有 spatial_block） | Roberts et al. 2017（block CV 方法学） | 高——`_spatial_block_folds` 可直接复用 | interpolation_compare.py + 各 loocv | 空间自相关场下 block CV RMSE ≥ index CV（诚实更差） | P2 |
| 8 | **conditional simulation（SGS，opt-in、caller_seeded）** | Gómez-Hernández & Journel 1993 | 中——numpy 可实现但为 draws × 邻域求解；建议 ≤100 draws + 小格网门 | kriging.py 新模块 + `random_seed_policy="caller_seeded"`；uncertainty 词表 monte_carlo_summary 已就绪 | 合成场：SGS 样本方差≈克里金方差、直方图≈数据直方图（复现统计） | P3 |
| 9 | **nested（双结构）变异函数**：γ=g1+g2 叠加拟合 | Webster & Oliver 2007 | 中——curve_fit 6 参数有界拟合，需防过拟合（n_lags 下限门） | kriging.py _fit_model 扩展 | 双尺度合成场 range 恢复；AICc 对单结构惩罚一致 | P3 |
| 10 | **全 Co-Kriging（MM2/线性模型 of coregionalization）** | Journel & Huijbregts 1978; Almeida & Journel 1994 | 中低——交叉变异函数拟合 + 大系统（k·2）²；MM1 已覆盖 80% 收益 | kriging.py collocated_cokriging 扩展 | 合成 LMC 场：full CK ≥ collocated ≥ OK（LOOCV） | P3 |
| 11 | **indicator 增强：序约束单调化 + ISDM 概率汇总** | Journel 1983; Goovaerts 1997 | 中——monotone rearrangement 是 O(T·C) 后处理 | kriging.py indicator_kriging（post-process 开关） | 单调性测试：p(t) 随 t 非降；E-type 对比 | P3 |
| 12 | **spatiotemporal kriging foundation**：product-sum 协方差 C_st=C_s·C_t+C_s+C_t | Cressie & Huang 1999; De Cesare et al. 2001 | 中低——需时间戳输入契约（time_field）与 3D 滞后 binning；建议先做 descriptor/契约预留 | 新 geo_analysis/spatiotemporal.py + descriptor（runtime_status=planned 起步） | 合成时空场 range_s/range_t 恢复；oracle 锚 | P3（foundation 预留可先行） |
| 13 | **approximate kriging 变体 + backend 窗口声明**（修 F5/F6） | covariance tapering: Furrer et al. 2006 | 中 | kriging.py + descriptor backend_variants 补 min/max_features | 同 conformance 套件 + 大 n 时间上界断言 | P2 |

## 9. 统一插值比较框架建议

现状：`interpolation_model_compare` 已实现"同数据、方法库级 CV（RMSE）、固定序 + cv_budget 走查、逐行披露跳过原因"的可比面板（idw/tin/trend/rbf/kriging），是良好地基。建议向"统一 validation + uncertainty + assumptions 输出契约"演进：

1. **统一 validation 契约**（全部插值工具的 `metadata.validation` 同形）：`ValidationMetrics{target, method∈loocv|k_fold|spatial_block, rmse, mae, bias, r2, folds, sample_count, subsampled(bool)+budget}`。现状 r2 只有 kriging CV 产出，IDW/RBF/TIN/trend/RK 的 LOOCV 应补 r2（残差/总方差同式可算，零风险增量）；`sample_count < n` 时强制 `subsampled=true` 披露（RK/RBF 已做，trend/tin 需对齐字段名）。
2. **统一 uncertainty 分型**（照 uncertainty.py 词表，禁止跨型冒充）：
   - kriging 家族（OK/UK/RK/CoK/BK）→ `raster_uncertainty`（方差/标准差面，附 interpretation 与负值钳制计数）+ 建议加 `prediction_interval` 摘要；
   - 确定性方法（IDW/RBF/TIN/trend/NN）→ `validation_metrics` + 残差分位数（`scalar_uncertainty/loocv_residual_quantiles`）；NN 诚实为空 + disclosures（现状正确）；
   - indicator → 概率面 + （修复 F2 后）typed 块 + 单调性披露；
   - 每个结果附 `uncertainty_calibration`（kriging 家族）：LOOCV z-score 的均值/分位（σ 可信度证据）。
3. **统一 assumptions 注入**：`build_evidence(descriptor,…)` 已把 assumptions/limitations 注入 scientific_evidence——model_compare 的比较行应携带每方法的 assumptions 指纹（如 exact-interpolator、stationarity、approximate 源），使"为什么选它"可审计。
4. **比较面板扩展**：(a) 纳入 indicator（概率面 Brier score）/natural_neighbor/nearest_neighbor（标注无 CV 语义的诚实跳过）；(b) 排名指标从单一 RMSE 扩为 `{rmse, r2, max_abs_error, uncertainty_calibration}`，平局打破保持确定性（方法名序）；(c) 建议给 RK/CoK 等 approximate 方法行加 `approximate=true` 标记与近似源说明，防止比较表把近似方法与精确方法无声混排。
5. **机器可读验收门**：把 §9.1–9.2 的字段形状固化为一个共享 schema 校验（pytest 参数化扫 14 工具结果），杜绝未来某方法"悄悄不产出 uncertainty/validation"。

---

## 附：核验命令与证据

- registry 校验：`get_algorithm_registry().validate()` → 0 issues（含科学元数据交叉校验、conformance 节点 AST 存在性）
- conformance：59/59 节点实存（自写 AST 扫描，与 registry 内置校验一致）
- 测试实跑：`pytest tests/unit/lib/test_{geostat_v3,interpolation_science_vnext,kriging_interpolation,regression_kriging,tin_interpolation,trend_surface,interpolation_compare,idw_interpolation}.py tests/unit/gis_harness/test_kriging_vertical_slice.py` → **134 passed**
- oracle：`tests/science_oracles/data/geostat.json` 253 cases（γ 闭式/各向异性/IDW/OK/驱动级独立复算）
- 关键文件：`app/lib/geo_analysis/kriging.py`（2435 行）、`app/lib/geo_analysis/interpolation.py`（809）、`app/lib/geo_analysis/regression_kriging.py`（552）、`app/lib/geo_analysis/tin_interpolation.py`（739，含 Sibson）、`app/lib/geo_analysis/rbf_interpolation.py`（370）、`app/lib/geo_analysis/trend_surface.py`（424）、`app/lib/geo_analysis/interpolation_compare.py`（277）、`app/tools/advanced_spatial.py`、`app/lib/gis/algorithms/interpolation.py`（960）
