# Science V5 — Baseline（Phase A 审计 · 2026-09-09）

- base SHA：`8a33e3a5`（origin/master，含 PR #1168 workflow-v4 合并）
- 最新 PR：#1172 quality-v2（merged）、#1171 extensions-v2、#1170 cartography-v5、
  #1169 workbench-v5、#1168 workflow-v4、#1167 query-v6、**#1166 science-v4（本 Epic 直接前身）**
- open issues：无与本 Epic 直接相关的未决 issue（gh 查询为空）。
- 并发 Epic 同域分支：无 `science-v5` / `geoai` 命名的现存本地或远端分支（`git branch -a` 核对）。

## 1. Science V4 现状（真实生产入口与唯一事实源）

| 能力 | 唯一事实源 | 生产入口 | 状态 |
|---|---|---|---|
| OK/UK/SK/KED 克里金 | `app/lib/geo_analysis/kriging.py` | `app/tools/advanced_spatial.py::kriging_interpolation`、`external_drift_kriging`（:197/:1849 注册面）+ `app/services/geocompute/ops.py` INTERPOLATION | native，V4 已交付 |
| SGS | `app/lib/geo_analysis/kriging_simulation.py` | `sgs_simulation` 工具（advanced_spatial.py:1849） | native；**逐节点 Python 循环**（:192-257，每节点 `np.linalg.solve` + 内层 cross-cov Python 循环 :234） |
| LMC 全共克里金 | `app/lib/geo_analysis/cokriging_lmc.py` | `cokriging_lmc_surface` 工具（:1981） | native；已构造批量 `(c,m,m)` 矩阵但**求解逐目标循环**（:318-335） |
| ST 克里金 | `app/lib/geo_analysis/kriging_st.py` | `st_kriging_surface` 工具（:2095） | native；**完全逐目标循环**（:195-236） |
| 变异函数 | `kriging.py::fit_variogram/_fit_model`（:451-580） | directional_variogram_analysis / variogram_model_selection 工具 | 有界 curve_fit + 确定性网格回退；嵌套模型 `fit_nested_variogram`（:2277）；**缺模型比较诊断表（RSS/AICc）与多起点全局优化** |
| CV | `kriging.py::cross_validate_kriging`（:1118） | kriging_interpolation 工具内部 | index + spatial_block 两方案、z-score 校准；**仅克里金；无时间感知 CV；无通用框架** |
| 水文 | `app/lib/geo_analysis/terrain.py`（:1454+） | terrain_analysis.py 工具面（breach/hand/stream/hypsometry/solar） | **Pfafstetter 单级**（:1724 显式披露多级未实现） |
| RS 时序 | `app/lib/geo_analysis/rs_v3.py::temporal_features`（:1482）、`sar_temporal.py` | remote_sensing.py 工具面（sar_temporal_stats 等） | 基础统计 + 单谐波；**显式披露：无物候、无 SG 平滑、无缺口处理、无质量掩膜整合** |
| Backend dispatch | `app/lib/gis/backend_selection.py::select_backend` | algorithm_resolver.py:103 消费 | 纯函数决策层成熟（ScaleProfile/BackendDecision/BackendEvidence）；**无内存预算驱动的 chunked 决策词表** |
| 资源治理 | `algorithm_registry.py`（ResourceEnvelope/NumericalTolerance/CancellationProfile/ApproximationClass）+ `scientific_errors.py`（17 类型） | descriptor + 实现层硬闸 | 成熟 |
| Oracle | `tests/science_oracles/`（冻结 JSON 回放）+ `scripts/gen_science_oracles.py` | `test_oracle_replay.py` | 10 域 corpus |
| Benchmark manifest | `scripts/gen_science_benchmark_manifest.py` → `docs/science/BENCHMARK_MANIFEST.md` | parity 测试锁定 | 声明面投影 |
| Ratchet | `app/lib/gis/algorithms/science_contract_v4.py`（44 项 allowlist，只许收缩） | `test_contract_drift_gate` / findings ratchet | 活跃 |

## 2. 与 Must-have 对齐的缺口（P0/P1/P2 分级）

- **P0-1（Scope B）**：CV 只有克里金专用实现；无方法无关框架、无时间感知 CV（future-leakage 防线）、无 leakage guard 测试、无 ST CV。证据：`grep time_aware/temporal_cv` 全库零命中（kriging.py:1047 唯一 CV 注释）。
- **P0-2（Scope D）**：三个向量化缺口 —— SGS 逐节点（kriging_simulation.py:192-257）、LMC 逐目标 solve（cokriging_lmc.py:318）、ST 逐目标（kriging_st.py:195-236）。规模上限因而被人为压低（SGS_MAX_TARGETS=20 万的注释明说「纯 Python 下不可完成」）。
- **P0-3（Scope F）**：不确定性散落在 SGS ensemble / kriging variance / CV z-score 三处，无统一 uncertainty artifact 契约（provenance/校准/渲染元数据/模型不确定性 vs 数据质量分离）。证据：`SGSEnsemble.to_dict`、`CokrigingResult.variances`、`CrossValidationReport.uncertainty_calibration` 各自为政。
- **P1-1（Scope G）**：物候/缺口处理/质量掩膜/时空立方体适配缺失（rs_v3.py:1544 disclosure 明示）。无 project-specific 硬编码（好）。
- **P1-2（Scope E）**：dispatch 无内存预算 → chunked 变体决策；新 batched 变体需要 descriptor 窗口 + 诚实 approximation_class。
- **P1-3（Scope I）**：多级 Pfafstetter、拓扑一致性校验缺失（terrain.py:1738 披露单级）。
- **P2-1（Scope C）**：variogram 模型选择只报 best RSS（fit_variogram:568-577），无逐模型比较诊断（RSS/AICc/参数合理性）表；无多起点全局优化的确定性回退语义文档化（网格回退存在但未进 oracle）。
- **P2-2（Scope H）**：新 solver 的内存估算/取消 checkpoint/cell cap 需与 ResourceEnvelope 对齐；SGS chunk 取消粒度逐 realization 已有，batched 后需重验。

## 3. 冲突面（与其他 9 个并发 Epic）

- 生成物：`docs/science/ALGORITHM_CATALOG.md`、`docs/science/BENCHMARK_MANIFEST.md`、`tests/science_oracles/data/*.json`（只追加 `science_v5.json`）、quality-manifest —— 结尾统一再生成。
- 共享文件：`app/tools/advanced_spatial.py`（追加新工具）、`app/lib/gis/algorithms/interpolation.py`/`temporal.py`/`terrain.py` descriptor（追加式）、`app/lib/gis/backend_selection.py`（纯函数扩展）、`CHANGELOG.md`（最小追加）。`app/tools/registry.py` 若需注册新工具模块要走 register_* 接线（检查 main.py 装配）。
- migration：无 DB 变更，零 Alembic 接触。

## 4. 测试为什么能证明正确

- oracle corpus：期望值开发期硬编码冻结，回放零重算 —— 证明「回归锚定」不证明「物理正确」；正确性由解析 case（手算装置）与 differential（reference vs optimized）补。
- `tests/quality/test_scientific_regression.py`：对抗性输入四类诚实结局红线 —— 是契约测试不是数值证明。
- V4 CV z-score 校准是真实生产路径（kriging.py:1209-1210 消费 res.variances）。
- SGS 确定性：同 seed 双跑逐位（V4 conformance）——batched 变体改变 RNG 消费序列，**必须**以 differential/statistical oracle 重新锚定而非伪装逐位一致。

## 5. Known limitations 复核（V4 PR summary 声明的）

- chunked Priority-Flood parity 已钉死（V4 交付）；SGS 20 万上限是**承认性能**的诚实上限 —— V5 batched 后可提高上限并改写 descriptor（如实声明新窗口）。
- LMC 结构分解 35/65 固定比例（非 Goulard-Voltz 完整迭代）——保持披露，不虚报。
