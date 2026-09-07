# Spatial Algorithm Foundation V3 — 深度审计（只读，基于 master@222a994）

> 审计方式：4 个并行只读审计 agent + 本地验证（registry 实测 128 算法 / 88 能力 / 62 契约）。
> 日期：2026-09-06。分支：feat/spatial-algorithm-foundation-v3。

## 0. 架构事实（全部审计一致确认）

- `app/lib/gis/algorithms/*.py` = **描述符注册表**（AlgorithmDescriptor + ParameterContract，ADR-0099），不含实现。
- 数学实现层 = `app/lib/geo_analysis/*.py`（纯 numpy/scipy）+ `app/services/network/*.py` + `app/services/rs/band_math.py`。
- 薄工具层 = `app/tools/*.py`（validate → apply_contract → 实现 → build_evidence → backend diagnostic）。
- 能力层 = `app/lib/gis/capabilities/*.py`（CapabilityDescriptor）；capability→algorithm 由算法的 `capabilities=[...]` 反向派生。
- 消费方：gis_harness planner/workflow_compiler（经 AlgorithmResolver）、workflow_engine、tool_dispatch_service（analysis reuse + cost wave）、session_plan、cartography 仅经 provenance.algorithm 字符串。
- 强制关卡：registry `validate()`（含 conformance test 节点 AST 校验）、`validate_algorithm_tool_parameter_parity()`（契约 required 参数必须出现在工具 OpenAI schema）、`scripts/gen_science_catalog.py` 字节级 parity 测试（tests/unit/gis/test_foundation_v2_infra.py）、`gen_workflow_catalog.py` parity、manifest fingerprint。
- 硬规则（CONTRACT_BACKBONE.md §10）：禁止 fake-native、禁止静默 proxy、禁止度当米用；planned 必须诚实拒绝。
- 科学依赖可用：numpy 2.4.6 / scipy 1.17.1 / sklearn 1.8.0 / esda 2.9.0 / libpysal / networkx / shapely / rasterio / pyproj。**无** pykrige / statsmodels / skimage。

## 1. 契约层真实缺口（Goal A）

| # | 缺口 | 证据 |
|---|------|------|
| A1 | 算法 `output_artifact_type` 未校验 ⊆ capability `output_artifact_types`（admin.boundary_lookup 输出 polygon_feature_set vs capability 声明 admin_boundary_set） | algorithm_registry.py:294-336; algorithms/data_access.py:51-57 |
| A2 | `algorithms_for_capability(include_planned=False)` 实际过滤 `unavailable` 而非 `planned`（命名/行为不符） | algorithm_registry.py:233-240 |
| A3 | `ScaleProfile.raster_cells` 被接受但从不消费——backend 决策没有 raster 规模/内存输入 | backend_selection.py:41-47 |
| A4 | ParameterContract.version 与 descriptor.contract_version 无一致性校验 | parameter_contracts.py vs algorithm_registry.py:122-123 |
| A5 | 31 个描述符无 ADR-0099 科学元数据（data_access/aggregation/density/raster/temporal/geometry legacy 包） | 审计A §6 |
| A6 | 过时注释：interpolation.py:23-28（kriging method 参数已存在）；terrain.py:280 与 tools/terrain_analysis.py:365 声称 D∞ 未实现（实际已实现） | 详见审计A/C |
| A7 | 三套 capability↔tool 视图共存（tool_to_capability / capability_tool_map / manifest）——manifest 为运行时权威，前两者须保持一致 | algorithm_registry.py:250-292 |

## 2. 统计域真实缺口（Goal B）

- **MGWR**：唯一 `runtime_status="planned"` 条目（algorithms/statistics.py:640-659），无实现。需真 backfitting，禁止 GWR 改名。
- **GeoDetector**：q 统计 + 交互探测 + F/permutation 已完成；**生态探测、风险探测缺失**。
- Gi* p 值为正态近似（已披露）；可补 permutation 选项。
- GWR 已有 LOO-CV 带宽 + AIC/AICc + local R²；**带宽诊断、局部共线性诊断缺失**。
- 多重检验校正（BH/Holm/Bonferroni）已完备。

## 3. 地统计/插值域真实缺口（Goal C）

- **完全缺失**：indicator kriging、co-kriging、最近邻插值、自然邻域（Sibson）插值。
- **方向变差函数（directional variogram）缺失**；模型 ranking/选择散落在 interpolation_compare。
- RK：目标点协变量 IDW 近似 + 方差不含趋势系数不确定性（已 honest flag approximate=True）——保留但强化披露。
- 变差函数模型已含 spherical/exponential/gaussian/matern(ν 固定)/wave/cubic + 各向异性变换 + 空间块 CV。

## 4. 点格局/时空域缺口（Goal D）

- **Space-time K 缺失**（仅 Knox）；Mantel 缺失。
- G/F 无边缘校正（raw CDF，已披露 `edge_correction:"none"`）→ 补 border/isotropic 校正版本。
- NNI 为正态近似（已披露）。
- 全部 permutation 已固定 seed=42，确定性良好。

## 5. 网络域现状（Goal E）

- 基本完备：Dijkstra/A*、OD、closest facility、service area（buffer envelope 已披露）、E2SFCA、gravity、Huff、p-median（枚举≤20k / Teitz-Bart）、p-center（greedy+vertex substitution）、centrality（exact≤2000 / sampled k=500 seed42）。
- **缺 exact LP/MIP backend**：scipy≥1.9 有 `scipy.optimize.milp`（本机 scipy 1.17.1 可用）→ 小规模 exact p-median/p-center 可加。
- 外部 API 工具（高德 isochrone/route/transit/traffic）诚实标注 EXPERIMENTAL/deterministic=False——不动。

## 6. 地形/水文域现状（Goal F）

- 几乎完备（24 描述符全 VALIDATED）。缺：epsilon flat-routing 内嵌 D8、sky view factor / horizon angle、MRVBF。
- 全 DEM 实内存（guard 250M px / 1 GiB / 50M hydro cells）；不做分块重写（越界，属 GeoCompute）。
- 度/米处理：`_metric_cell_sizes` cos(lat) 修正已存在并有测试。
- 修复项：terrain.py:280 与 terrain_analysis.py:365 过时"D∞ 未实现"声明；roughness 引用差异在 descriptor 补注。

## 7. 遥感域缺口（Goal G）

已存在：11 指数框架、tasseled cap、PCA、GLCM（9 Haralick）、变化检测（diff/CVA/ratio/log-ratio/阈值）、时间合成、STAC cloud_cover 场景选择。
**完全缺失**：MNF、ICA、SAM、SID、matched filter、RX anomaly、MAD/IR-MAD、分割基础、云/阴影辅助接口。
输出契约：raster artifact + stats + bounded sample + evidence 已统一；`recommended cartographic model` 仅 compatible_map_models——新算法沿用该机制（建议不写死）。

## 8. SAR 域缺口（Goal H）

已存在：β⁰/σ⁰/γ⁰ 定标（标量/平面入射）、Lee/Refined-Lee(MSE 代理，已披露)/Frost、时间合成（mean/std/median/percentile）、VV/VH 比值。
**缺失**：逐像素入射 LUT 定标、热噪声去除、多时相 speckle（Quegan/MT-Lee）、相干性估计、地形校正/RTC、layover/shadow 掩膜、Gamma Map、Kuan、对数缩放形式化。

## 9. Backend/Scale 统一（Goal J）

- `select_backend(algorithm_id, ScaleProfile)` 纯函数已统一；仅 2 个真实双 variant（centrality、kriging solve）。
- 升级点：ScaleProfile 消费 `raster_cells` + `estimated_bytes` + memory budget；决策输出已含 rationale/diagnostics/fallback——保持形状，扩展输入与变体窗口。

## 10. Oracle Corpus 现状（Goal K）

- 已有 golden/conformance 测试（esda 对照 1e-10、手算 fixture、test_golden_gis_numerics.py），但**无系统化 1000+ 用例语料**。
- 方案：`scripts/gen_science_oracles.py` 生成 → `tests/science_oracles/` 分域回放；期望值由参考实现（scipy/sklearn/esda/手解析）一次性生成硬编码，运行时纯回放（确定性、无网络）。

## 11. 规模基线与目标

| 维度 | 现状 | V3 目标 |
|------|------|---------|
| algorithms | 128 | ≥180 |
| capabilities | 88 | ≥120 |
| contracts | 62 | ≥100 |
| oracle cases | 散落 | ≥1000 |
