# Science V5 — Architecture（Phase B 冻结稿）

## 0. 定位

V5 = **可验证、可扩展、可并行、带不确定性的科学计算**。全部为既有主链路
（kriging/cokriging_lmc/kriging_st/kriging_simulation/terrain/rs_v3/backend_selection）
的纵向深化 + 3 个新模块（cv / uncertainty / temporal_cube+phenology）。
**零第二事实源**：新模块消费既有实现，不复制数值逻辑。

## 1. 组件图（新增 ● / 修改 ○）

```
app/lib/geo_analysis/cv.py            ● NEW  CV 框架唯一事实源（splitter + metrics + leakage guard）
    ├── spatial_block_folds           （从 kriging._spatial_block_folds 上收；kriging 改为 re-import 同一实现）
    ├── temporal_forward_folds        ● 时间前向链（fold k: train=过去块, test=块 k；零 future leakage）
    └── run_cross_validation          方法无关编排（fit/predict 回调；泄漏守卫断言进 report）
app/lib/geo_analysis/kriging.py       ○ _spatial_block_folds = cv.spatial_block_folds（别名 re-import 同一对象）；
                                        _fit_model 失败路径确定性 multi-start polish（成功路径逐位不变）；
                                        select_variogram_model + 逐模型 diagnostics 旗标（不新增第二比较入口）
app/lib/geo_analysis/cokriging_lmc.py ○ 逐目标 solve → 批量 np.linalg.solve((c,m,m),(c,m))；
                                        LinAlgError 时逐行隔离退化（计数语义不变）
app/lib/geo_analysis/kriging_st.py    ○ 逐目标循环 → 定长 k 填充批量系统（(c,k+1,k+1)）；窗口不足
                                        目标走 padding+掩膜 + 原语义回退（degraded 计数不变）；
                                        + st_cross_validate（时间前向链 + 空间块混合）
app/lib/geo_analysis/kriging_simulation.py ○ SGS 双实现：
                                        reference = V4 逐节点逐实现路径（逐位保留，backend="numpy"）
                                        batched   = 共享路径 + chunk 批量求解（covariance 与实现无关
                                                    → 每 chunk 一次 solve，权重跨实现复用，
                                                    噪声/条件值逐实现独立；backend="numpy_batched"）
                                        SGSEnsemble.backend 字段落 variant id；近似披露更新
app/lib/geo_analysis/uncertainty.py   ● NEW UncertaintyArtifact（统一不确定性契约）
app/lib/geo_analysis/temporal_cube.py ● NEW 时空立方体适配（SAR/光学统一时间轴/质量掩膜/缺口披露）
app/lib/geo_analysis/phenology.py     ● NEW 物候特征引擎（SG 平滑 + 双谐波 + SOS/EOS/LOS/峰值）
app/lib/geo_analysis/terrain.py       ○ + pfafstetter_codes_multilevel（≤4 级；主干走法上收为共享
                                        helper，levels=1 与单级逐位一致）+
                                        validate_flow_topology（环/悬挂/越界/汇流违例/平台报告）；
                                        单级函数 meta 契约保留（V4 测试锚定）
app/lib/gis/backend_selection.py      ○ + plan_execution（纯函数；native/vectorized/chunked 词表 +
                                        chunk_shape 建议；distributed 明示 planned 不存在）
app/lib/gis/algorithms/interpolation.py ○ sgs/cokriging_lmc/st_kriging descriptor + numpy_batched 变体窗口
app/lib/gis/algorithms/temporal.py    ○ + temporal.phenology / temporal.cube_stats / temporal.anomaly descriptor
app/lib/gis/algorithms/terrain.py     ○ + terrain.pfafstetter_multilevel / terrain.flow_topology_validate descriptor
app/tools/science_temporal_tools.py   ● NEW 物候/立方体/异常工具面（独立模块，避免 remote_sensing.py 冲突）
app/tools/terrain_analysis.py         ○ + multilevel_pfafstetter / flow_topology_validation 工具
app/tools/__init__.py                 ○ +1 行注册 science_temporal_tools
```

## 2. 关键决策

### D1 — CV 框架（Scope B）
- **单一事实源**：splitter（fold 分配）与 metrics 聚合在 `cv.py`；
  kriging 的 `cross_validate_kriging` 保留签名与数值行为（oracle 已锚定），
  但 `_spatial_block_folds` 上收为 `cv.spatial_block_folds`（kriging re-import，
  兼容别名 `_spatial_block_folds = spatial_block_folds`）—— 消除 split 双源。
- **splitter 三族**（全确定性，无 RNG）：
  - `index`：`arange(n) % folds`（兼容既有）；
  - `spatial_block`：排序秩 → ⌈√folds⌉ 网格 → `block % folds`（V4 语义上收）；
  - `temporal_forward`：时间升序切块（**边界只落在唯一时间值边界**——
    同一时刻的样本永不跨折拆分，挑战 M9；unique 时间值数 < folds →
    类型化诚实拒绝；排序用稳定 argsort 保样本恒等），
    fold k 的 train = 全部 < t_k 的样本（expanding window）——**前向链**，
    `report.leakage_check = {"max_train_time < min_test_time": true}` 逐折断言。
- **方法无关编排**：`run_cross_validation(coords, times|None, values, fit_fn,
  predict_fn, scheme, folds)` → `CrossValidationReport`（rmse/mae/bias/r2 +
  per-fold + calibration z-scores（predict_fn 返回 (pred, var) 时）+
  leakage 证据 + insufficient-sample 诚实披露）。st_kriging 的时间维度
  通过同一样本 (x,y,t) 元组自然支持 space-time CV（Scope G）。
- **泄漏守卫**（测试期硬断言，非运行时 raise）：
  - spatial：逐折 train_block ∩ test_block = ∅（构造保证，测试钉死）；
  - temporal：逐折 max(train_t) < min(test_t)；
  - duplicate-coordinate 跨折计数进 report（聚类采样诚实性披露）。

### D2 — Variogram v5（Scope C）
- `compare_variogram_models(pts, values, models=AUTO+opt-in)` →
  `VariogramModelComparison`：逐模型 VariogramFit + 加权 RSS + AICc
  （k=3，n=len(lags)；n−k−1≤0 时 AICc=None 披露）+ 合理性旗标
  （range > 2·span / sill > 4·var / nugget ≥ sill）+ 确定性排序。只读诊断，
  **不改变** fit_variogram 主路径（oracle 锚定）。
- `_fit_model` 失败/退化路径：确定性 multi-start（p0 从经验 gamma 分位数
  导出的固定起点集）polish 后再落网格回退；成功路径逐位不变。

### D3 — 批量求解器（Scope D）—— 本 Epic 性能核心
- **LMC**：`(c,m,m)` 矩阵已构造，改为单次 `np.linalg.solve(C, rhs)`
  （numpy 对堆叠矩阵逐片调同一 LAPACK 例程——well-conditioned 结果与
  逐片解**同环境逐位一致**（实现性质非 API 保证，differential oracle
  钉死））。**隔离条件 = LinAlgError ∨ 非有限**（挑战 M3）：批量解后
  `np.isfinite(sol).all(axis=1)` 掩膜——近奇异片会静默解出巨大有限值，
  必须逐行重解隔离 + V4 原回退（邻域均值，degraded 计数）；
  oracle 必须同时植入精确奇异行与数值退化行。
- **ST**（架构挑战 C1/C2 修订）：邻域定长化按**逐目标 relaxed 列表**
  ——窗口内邻居 ≥2 用 `idx[:k]`，<2 用纯空间 `i_all[:k]`（V4 语义：
  relax 后仍解克里金系统，只有 solve 失败才邻域均值）——不足 k 的槽位
  哨兵填充：`C[pad,valid]=C[valid,pad]=0`、`C[pad,pad]=I`、rhs[pad]=0，
  **约束/漂移行列同样置零**（否则 Σw=1 行把 μ 拉进 pad 权重、改变全部
  有效权重——C2）；解后只从 valid 掩膜位取 pred/var。邻域均值 fallback
  仅用于孤立 solve 失败（LinAlgError/非有限），degraded 计数语义不变
  （LMC 计 `var<0`、ST 计 `var<=0`——两者边界既有差异保留）。
  oracle：3 邻域手算 toy（pad vs 逐目标）+ 无 pad 行逐位 + pad 行容差。
- **SGS batched**（关键洞察；挑战 M4/M5 修订）：V4 的 chunk 语义 = chunk
  开始时重建条件树（chunk 内节点互不可见）——**这已经是
  block-conditional-independence 近似**。batched 变体把它推到自然结论：
  1. 实现**分组共享路径**：R 个实现分 P 组（默认 P=8，可配），组内共享
     随机路径、组间路径不同——组间路径方差重新进入 ensemble（M4：
     单一路径会系统性低估 std——`E[Z|path]` 丢掉 between-path 分量）；
     solve 数从 O(R·T) 降到 O(P·T/B)；
  2. 协方差矩阵只依赖几何 → 每 (group, chunk) 一次 `(B, m, m)` solve，
     权重/方差组内跨实现复用；
  3. 逐实现：`pred_r = w @ cond_vals_r`（einsum 批量）、
     `draw_r = pred_r + √var · ε_r`（ε 逐实现独立，路径序消费）。
  - **reference 实现逐位保留**（variant id "numpy_reference"；backend
    词表仍 "numpy"——挑战 M7：`numpy_batched` 不是合法 backend 词表成员，
    区分度放 variant id）；两实现各自确定性（同 seed 逐位）+ **统计
    differential oracle**（ensemble mean 相关 ≥0.99、std 比 ∈[0.9,1.1]、
    P50 差 ≤ MC 容差）——不伪装逐位一致（RNG 消费序列不同，如实声明）。
  - 上限（M5 修订）：reference 路径保持 `SGS_MAX_TARGETS=20 万`；
    batched 路径目标上限 **动态** = `SGS_MAX_ENSEMBLE_CELLS / R`
    （ensemble 预算才是真约束——固定 100 万窗口在 R=100 时不可达，
    且 realizations 工作矩阵 O(R·T) 是必然内存：1M×100 float64=800MB，
    预算表如实列出；超限 → ResourceScaleMismatch，负例双上限都测）。
  - chunk 大小 B=256（内存 (256, 2·24, 2·24)·8B ≈ 4.7MB @ k=24 上界）；
    有界。
- 取消：LMC/ST 按 chunk checkpoint（`cancellable(range(...), every=1)`）；
  SGS batched 按 realization checkpoint（与 V4 同粒度）。

### D4 — Scalable Backend Dispatch（Scope E）
- `backend_selection.plan_execution(algorithm_id, scale)` 纯函数 →
  `ExecutionPlan{variant_id, backend, chunk_shape|None, memory_note,
  approximation_disclosure}`：变体窗口命中（既有 select_backend 语义）
  + ResourceEnvelope 线性内存估算超 2 GiB → chunk_shape 建议
  （如 SGS 按 (realizations_batch, cell_chunk)）。
- **诚实词表**：native / vectorized（variant id "numpy_batched"，backend
  字段仍 "numpy"——BACKEND_VOCABULARY 封闭词表不扩）/ chunked（建议）；
  **distributed 不存在**（planned，词表不收、不虚构——分发执行 seam 留在
  Descriptor.backend_variants 声明层，无假实现）。
- **窗口单位（挑战 M6 修订）**：sgs/lmc/st 的变体窗口按**目标格点/cell
  空间**声明并在 descriptor notes 写明「窗口单位 = raster_cells（目标
  格点），调用方以 `ScaleProfile(raster_cells=n_cells)` 触发」——
  feature_count（样本数）不是这些算法的规模瓶颈；reference 变体窗口
  下界 = 8（InsufficientSamples 硬闸），非 1。测试钉死
  `select_backend(algo, ScaleProfile(feature_count=None, raster_cells=N))`
  的变体选择。
- descriptor 更新（additive）：`interpolation.sgs` 增 `numpy_batched` 变体
  + reference 变体；**窗口交叠、声明序偏好生效**——`numpy_batched` 声明
  在前 → auto 在全部规模选 batched，reference 经显式 backend 参数 opt-in
  （实现期决策，R1-#4 修订）；`interpolation.cokriging_lmc` / `interpolation.st_kriging`
  增批量变体；numerical_tolerance 措辞 variant-scoped（reference = 同 seed
  逐位；batched = 同 seed 逐位（自身）+ 对 reference 统计 differential）；
  新增算法全量声明 envelope/cancellation/tolerance（ratchet 自动约束）。
- **工具接线（挑战 M10）**：sgs/cokriging_lmc/st 工具消费
  `select_backend`/`plan_execution` 并把 BackendEvidence 挂进 metadata
  （现只有 kriging_interpolation 接线）——否则变体是死元数据；
  需要的参数 additive 进 PARAMETER_CONTRACT（parity gate 同步）。

### D5 — Uncertainty Artifact（Scope F）
- `UncertaintyArtifact` dataclass：`estimator`（"kriging_variance" |
  "sgs_ensemble" | "st_kriging_variance"）、`std/quantiles{p10,p50,p90}`、
  `ci{level,low,high}`、`provenance{seed,n_realizations,variogram,backend,
  variant,tolerance}`、`calibration{z_score_mean,z_coverage_95}|None`、
  **`model_uncertainty` vs `data_quality` 显式分离字段**（前者=模型方差/
  ensemble 离散度；后者=样本密度/nodata 覆盖披露，绝不混合成单一数字）、
  `disclosures`、`to_renderer_metadata()`（value_semantics + 分位数断点
  建议给渲染分档）。
- 构建：`from_sgs(ensemble)` / `from_kriging(preds, variances, cv_report?)` /
  `from_st(st_result)`；surface 工具输出挂 `metadata["uncertainty"]`
  （有界摘要）+ records 逐格字段保持既有（additive）。
- **契约钉死（挑战 M13）**：`estimator` 字段**必填无默认**（渲染方不得
  跨估计器混读模型不确定性——`sgs_std`=ensemble 实现间离散度（后向变换
  域）vs `ck/st_variance`=克里金方差是不同估计器）；R<2 时 std 诚实为
  None + 披露（不是 0——伪精确）；`data_quality` 只来自真实样本密度/
  nodata 元数据，绝不合成。能力对接：`uncertainty_outputs` ∈ 既有
  `UNCERTAINTY_TYPE_VOCABULARY`（app/lib/gis/uncertainty.py:19）。

### D6 — 时空立方体 + 物候（Scope G）
- `TemporalCube`：`stack(T,H,W) + times_sec(T) + quality(T,H,W)|None +
  nodata`；构造校验（T 单调、缺时间片→gap 披露列表）；`from_sar_stack` /
  `from_optical_stack` 适配（SAR 接 sar_temporal 的 comparability 语义、
  光学接 cloud_qc_basic 质量掩膜）。cap：T≤512（ResourceScaleMismatch）。
- `phenology_features(cube, ...)`：逐像元 Savitzky–Golay 平滑（窗口有界
  奇数、polyorder≤3；缺口 ≤ max_gap 线性插值并计数披露）→ 双谐波
  LS → 阈值法（振幅分数）SOS/EOS/LOS/peak_value/peak_time +
  谐波相位一致性诊断。全部 nan-aware；不引入项目专属类别。
- `temporal_anomaly(cube, baseline=None)`：逐像元 climatology（按 doy 窗
  或全期）z-score 异常 + 两期差分显著性（n≥2 披露）。
- 工具面：新模块 `science_temporal_tools.py`（`phenology_features` /
  `temporal_anomaly` / `temporal_cube_stats`）——descriptor 注册在
  temporal 域（temporal.phenology 等）， PARAMETER_CONTRACTS 同步。

### D7 — Hydrology（Scope I）
- `pfafstetter_codes_multilevel(d8, acc, outlet, levels=2)`：单级编码
  （V4 机器复用）+ 递归子盆地（每级对 inter-basin 以最大积水支流为
  outlet 重入；levels≤4 硬顶；cell guard 复用）；输出 codes + level 掩膜
  + 披露。与单级路径 differential：level=1 时 codes 与 V4 单级一致。
- `validate_flow_topology(d8, acc)`：环检测（receiver 链步进上限 = 格数，
  有界）、悬挂 receiver（指向 nodata/越界）、多出口、积水单调性违例
  计数 → 结构化报告 + disclosures（不 raise——拓扑事实，非异常）。
- oracle terrain fixtures：合成圆锥 DEM + 已知双级河网，钉死两级编码。

### D8 — 兼容与回滚
- 所有修改 additive 或**数值逐位保持**（LMC/ST batched 与逐位一致用
  differential 测试钉死；SGS reference 路径逐位保留，batched 是新变体）。
- 工具输出新字段 additive（metadata.uncertainty / backend evidence）。
- 无 DB/migration 变化；生成物结尾统一再生成（catalog/manifest/oracles）。
- 回滚 = revert 单分支；无状态迁移。

## 3. 资源/复杂度预算（batched 后）

| 路径 | 之前 | 之后 |
|---|---|---|
| LMC solve | O(T) 次 Python solve m=k1+k2+2 | O(T/512) 次批量 solve；无逐目标循环 |
| ST solve | O(T) 次（含逐目标矩阵构造） | O(T/512) 次批量；构造向量化 |
| SGS solve | O(R·T) 次 m 维 solve + 逐节点 cross 循环 | O(T/B) 次 (B,m,m) 批量 + O(R·T·m) einsum |
| SGS 内存 | O(T)（逐实现流式） | O(R·T) 输出必然 + O(B·m²) 工作集（有界）|
| SGS 上限 | 20 万格点（承认性能） | batched 100 万（新窗口如实声明）|

Benchmark 用 work-count（solve 调用数 monkeypatch 计数）+ 结构性数组
字节估算；wall clock 仅辅助（仓库哲学一致）。

## 4. 测试 oracle 策略

- **解析 case**：球状模型已知参数的 OK 手算锚（V4 已有，保留）；
  V5 新增：AICc 手算、temporal_forward 折分配手算、多级 Pfafstetter
  已知编码、物候合成正弦 SOS/EOS 解析期望、batched LMC/ST 与 reference
  逐位 differential、SGS 两 backend 统计 differential。
- **负例**：fold 数 > n、temporal 常量时间、SGS 上限拒绝、T 超限、
  病态批量系统隔离回退、pfafstetter levels>4、cube 时间倒序。
- **边界**：n=folds 恰好、单时间片、全 NaN 像元、k > n、chunk > T。
