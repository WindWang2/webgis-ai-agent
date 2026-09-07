# Spatial Algorithm Foundation V3

> 状态：随 `feat/spatial-algorithm-foundation-v3` 合入。基线 master@222a994。
> 开发账本：`docs/dev/spatial-algorithm-v3/`（audit / plan / decisions / patterns / progress）。

## 0. V3 是什么

Foundation V2（ADR-0099）建立了"能力 → 算法 → 工具"的科学契约底座与第一批
域扩展。V3 的目标不是机械增加算法数量，而是把算法库升级为可被 GIS Harness、
Workflow、Tool Runtime 与 Cartography **稳定依赖的专业科学计算底座**：

1. **执行契约统一收紧**（正确性/一致性门）；
2. **关键 planned/incomplete 能力实质推进**（MGWR backfitting 等）；
3. **专业域补链**（遥感目标检测/异常/变化、SAR 预处理链、时空点格局、
   地统计非平稳/概率插值、网络精确求解、地形辐射与几何校正）；
4. **backend/scale 决策层统一消费规模画像**；
5. **Numerical Oracle Corpus**（确定性数值回归语料）。

## 1. 执行契约 V3 变更（全部向后兼容）

| 变更 | 语义 |
|------|------|
| 输出⊆能力校验 | `registry.validate()` 现要求算法 `output_artifact_type` 是其每个能力的 `output_artifact_types` 成员（此前只有存在性校验）。唯一历史违例 `admin.boundary_lookup` 通过扩能力输出修复（未弱化校验）。 |
| include_planned 语义 | `algorithms_for_capability(include_planned=False)` 现同时过滤 `planned` 与 `unavailable`（此前命名与行为不符）。 |
| ScaleProfile 消费 raster_cells | `feature_count` 缺省时以 `raster_cells` 折算规模窗口/分层（栅格主导型算法的真实调用形态）；新增可选 `estimated_bytes`：超 2 GiB 预算在 rationale 追加内存注记（建议性诊断，硬闸仍在实现层 `ResourceScaleMismatch`）。 |
| 描述符元数据补齐 | 全部 171 个算法现都有 `algorithm_family` + `crs_class` + seed 策略 + assumptions/limitations；`scientific_status` 除 8 个无专属 conformance 测试的描述符外全部声明（无测试的诚实留空，不伪造 VALIDATED；外部 API 客户端诚实 `EXPERIMENTAL`）。 |
| 过时声明清理 | 中央 kriging 契约已含 method 枚举（旧注释作废）；D∞ "未实现" 声明更正；roughness 引用差异（Wilson max−min vs 窗口 std）在描述符内如实标注。 |

## 2. V3 新算法族（按域）

### 2.1 空间统计
- `spatial.mgwr` — **MGWR backfitting**（Fotheringham 2017）：逐变量带宽 + backfitting
  迭代 + ENP/AICc；等带宽收敛于 GWR 解（conformance 锚）。
- `spatial.geodetector_ecological` / `spatial.geodetector_risk` — q 差异 t 检验 / 分层均值
  Welch 比较（Wang 2010 族）。
- `spatial.local_join_count` — Anselin & Li 2019 无自邻接二元局部检验（条件置换 + BH）。
- `spatial.bivariate_local_moran` — esda Moran_Local_BV 诚实委托（岛屿权重披露）。
- `spatial.weights_diagnostics` — 连通分量/孤岛/对称性/邻居数分布。
- Gi\* permutation p 选项（缺省 normal 行为不变；contract v3 additive）。

### 2.2 地统计与插值
- `interpolation.indicator_kriging` — 多阈值概率面 + p50 阈值面 + E-type（Journel 1983）。
- `interpolation.cokriging` — collocated co-kriging（MM1；|ρ|<0.2 类型化拒绝）。
- `interpolation.nearest_neighbor` / `interpolation.natural_neighbor` — Voronoi 值填充 /
  Sibson（scipy Delaunay，凸包外 NaN）。
- `interpolation.directional_variogram` / `interpolation.variogram_selection` — 方向变差函数
  （数学约定方位角，披露）+ 六模型加权 RSS/AICc ranking。
- `interpolation.block_kriging` — 块支撑（2×2 离散化，I&S 1989，披露）。

### 2.3 点格局 / 时空
- `point_pattern.space_time_k` — Diggle 1995 时空 K（独立参考 πr²·2t；时间置换；
  空间各向同性校正；时间边缘未校正如实披露）。
- `point_pattern.mantel` — 标准化 Mantel r + 时间标签置换（n≤2000 密集矩阵诚实上限）。
- `point_pattern.cross_pcf` — g12(r)（cross-K 导数 + Epanechnikov；随机标记包络）。
- G/F 边缘校正：`edge_correction = none|border|isotropic`（缺省逐位不变；border 内点不足
  类型化拒绝；isotropic 与 K 同款 Ohser 权重）。

### 2.4 网络
- `network.pmedian_exact` / `network.pcenter_exact` — scipy MILP（HiGHS）精确解；
  规模上界外类型化拒绝回启发式（不静默）；solver/目标值/mip_gap 披露。
- `network.eigenvector_centrality` — 幂迭代（收敛 δ/迭代数披露；networkx conformance）。

### 2.5 地形
- `terrain.horizon_angle` / `terrain.sky_view_factor` — 方位角地平线行走 + Steyn 1980
  SVF（共用射线行走实现；平地 SVF≡1）。
- D8 `flat_routing="epsilon"` — epsilon 填洼表面上的平地路由（缺省逐位不变；
  Barnes 2014 机制复用）。

### 2.6 遥感
- `remote.mnf` — 噪声白化 PCA（Green 1988；局部差分噪声估计披露）。
- `remote.ica` — FastICA（seed=42；收敛披露）。
- `remote.sam` / `remote.sid` — 光谱角 / 光谱信息散度匹配。
- `remote.matched_filter` / `remote.rx_anomaly` — 协方差白化目标检测 / 全局 RX 异常。
- `remote.mad_change` — MAD / IR-MAD（CCA + 迭代重加权，Nielsen 1998）。
- `remote.segmentation` — 空间-光谱 seeded KMeans 分割基础（非 SLIC，披露）。
- `remote.endmember_vca`（EXPERIMENTAL）、`remote.band_correlation`、
  `remote.temporal_features`、`remote.robust_normalize`、`remote.cloud_qc`（EXPERIMENTAL，
  明确非 Fmask）。

### 2.7 SAR
- 逐像元入射 LUT 定标 / 热噪声底去除（标量或 LUT 通道，additive）。
- `sar.multitemporal_speckle` — 强度域 MT-Lee（非 Quegan 谱域，披露）。
- `sar.coherence` — 复数 SLC 对相干性（无复数通道即拒绝，不伪造）。
- Gamma Map / Kuan 滤波（speckle 枚举扩展）。
- `sar.rtc` — 地形辐射校正（γ-flattening，Small 2011）。
- `sar.layover_shadow` — 局部入射角几何判据掩膜。
- `sar.log_scaling` — amp↔intensity↔dB 形式化转换；ENL 估计图。

## 3. Backend / Scale 决策（统一层）

- 所有新算法经 `select_backend(algorithm_id, ScaleProfile(...))` 出具诊断；
  栅格主导型算法传入 `raster_cells`（V3 起为真实决策输入）。
- `backend_variants` 只声明实现里真实存在的路径（kriging numpy/scipy 求解、
  centrality exact/sampled Brandes、p-median/p-center MILP）；Gi\* 的
  normal/permutation 与网络 exact/heuristic 分别是参数契约枚举和独立算法 id
  （配合 fallback_semantics），不虚构成 variant。
- 决策输出形状不变（BackendDecision / rationale≤160 / to_diagnostic），完全向后兼容。

## 4. Numerical Oracle Corpus

- `tests/science_oracles/data/<domain>.json`：确定性用例（fixture + 期望值）；
  期望值来自**独立参考公式**（numpy/scipy 解析解、手算黄金值）或标注
  `anchor=true` 的实现回归锚。
- `tests/science_oracles/test_oracle_replay.py`：零重算回放（exact / allclose /
  error 三种语义 + `select` 提取迷你语言）。
- `scripts/gen_science_oracles.py`：生成器（期望一次真相；JSON 是冻结快照）。
- 规模契约：≥1000 case（分域构成见 data/ 目录与 PR body）。

## 5. 兼容性

- 全部既有算法输出键、契约 required 参数、工具签名 **不变**（contract 版本号按
  additive 规则 bump；旧调用方行为逐位不变，由既有测试 + oracle 回放共同锁定）。
- `planned` 条目唯一（无了）：MGWR 由 planned 翻转为 native。
- 中央文件（algorithm_registry/parameter_contracts/manifest）只做授权的校验收紧
  与词表扩展；域包仍是唯一事实源。

## 6. 已知限制（诚实口径）

- SAR：无复数 SLC 谱域处理（Quegan MT 滤波、真实基线相干性需要 SLC 通道——
  相干性工具只接受复数 I/Q 输入）；RTC 用局部入射角平面/DEM 近似。
- 遥感：云检测为亮度阈值基础（EXPERIMENTAL），非 Fmask/cloud-probability；
  分割为 KMeans 基础（非 SLIC/河湖分割语义）。
- MGWR backfitting 收敛到局部最优（无全局最优保证，meta 披露）。
- co-kriging 为 MM1 collocated 近似（完整 LMC 不做）。
- 全部 oracle 期望值生成环境：numpy 2.4.6 / scipy 1.17.1 / sklearn 1.8 / esda 2.9。

## 7. 后续（follow-ups）

- 窗口化地形流水线（当前全 DEM 实内存 + guard；分块执行属 GeoCompute 域）。
- 云/阴影物理链（热红外/时序合成）——需要 artifact 元数据模型扩展。
- workflow recipe 面向新能力的编排模板（本分支不动 workflow compiler）。
- oracle corpus 持续扩充：新域合并后按同一生成器模式追加。
