# 01 Registry Census（全量算法普查）

> 基线：origin/master = 16d1c70（分支 feat/spatial-science-geoai-platform-v3）。
> 审计方式：8 个只读审计 agent（地形/光学/地统计/基建/空间统计/SAR/网络/点格局）+ registry 程序化导出。
> 逐域详表见 `domains/01..07-*.md`；程序化快照见 `census_snapshot.txt`。

## 总量（基线 16d1c70 实测）

| 维度 | 数量 |
|---|---|
| 算法（AlgorithmDescriptor） | 171（全部 runtime_status=native，无 planned/unavailable 残留） |
| 能力（CapabilityDescriptor） | 112 |
| 参数契约 | 106 |
| 方法引用（METHOD_REFERENCES） | 103（Wave 修复后 105+） |
| 科学前置 checker | 17 固定 + 参数化族 |
| conformance 测试文件 | 77 个（节点级 AST 校验全过） |
| oracle 语料 | 1084 例 / 12 域（tests/science_oracles/） |

成熟度分布：VALIDATED 149 · EXPERIMENTAL 13 · PRODUCTION 1 · 未标注 8。
approximate=True 仅 8 个（地形/网络域大量近似方法未使用该维度——域内缺口）。

## 域分布（category → 算法数）

interpolation 15 · network_analysis 18 · point_pattern 13 · remote_sensing 34 ·
spatial_regression 6 · spatial_statistics 17 · temporal_analysis 8 ·
terrain_analysis 26 · geometry_processing 8 · raster_analysis 4 · density/aggregation/data_access/decision 等 22。

## 域级审计结论汇总

| 域 | 审计文件 | fake-native | 最高严重度 finding | 科学质量评价 |
|---|---|---|---|---|
| 空间统计 | domains/01 | 无硬编码类 | h3_hotspot 接线不实（M）；join_count 口径漂移（M） | MGWR 为真 backfitting；GeoDetector 四探测齐 |
| 地统计/插值 | domains/02 | 无 | indicator 阈值守卫检查对象错误（M，已修复） | 真克里金（binning→加权LS→k邻域系统→真方差）；IDW 不冒充方差 |
| 点格局/时空 | domains/03 | 无 | temporal.hotspot 语义失配（M）；pcf NaN 入 JSON（P1） | 边界校正/置换契约逐项与文献一致 |
| 网络/优化 | domains/04 | 无（proxy 如实声明） | p-median 错引 MCLP 论文（High）；启发式族缺规模闸 | HiGHS 在手，exact MILP 真实；断连图诚实 inf |
| 地形/水文 | domains/05 | 无 | openness/horizon 联合内存爆炸（P0，已修复）；水文组合链断裂（P0，已修复） | Priority-Flood/D∞/Strahler 实现与文献一致 |
| 光学遥感 | domains/06 | 无实质 | 本地路径位置猜波段（HIGH，已修复 strict） | tasseled cap 系数与发表值逐位一致 |
| SAR | domains/07 | 无 | vh_ratio 文案宣称不存在的 dB 路径（M）；可比性守卫死代码（M） | Gamma-MAP MAP 方程独立求导验证；β⁰/σ⁰/γ⁰ 黄金值 |
| 基建/契约 | domains/08 | — | uncertainty producer-test 无机器校验（已修复） | validate() 覆盖广；catalog 字节级 parity |

## Wave 1 后新增的 registry 能力（本分支）

- Backend SDK V3：ResourceEnvelope / NumericalTolerance / ApproximationClass /
  CancellationProfile / 变体级精度分类 / BackendEvidence（ADR-0117）。
- Wave 8：uncertainty_producer_tests 映射 + validate() 机器校验
  （declared uncertainty → producer test 存在性）。
- 插值域 14 descriptor 补 complexity + approximation_class；kriging 变体
  补规模窗口（选择层真实可判别）。
