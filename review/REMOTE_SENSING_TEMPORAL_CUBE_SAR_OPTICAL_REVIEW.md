# REVIEW — RS Temporal Cube & SAR-Optical Fusion Harness

- Branch: `rs/temporal-cube-sar-optical-v1`
- Baseline: `origin/master = faa453a8935101378c23eb6694a42c3616d9c670`
- Review round: R1（独立 adversarial review，subagent B，2026-09-16）
- Review verdict: **P0×1 / P1×2 / P2×2 / P3×7 —— 全部处置，无未处理 P0/P1**
- 处置 commit: `134d3586`（红测 → 修复 → 回归）

## 处置矩阵

| ID | 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| P0-1 | P0 | `rs_features._sen_slope` / `temporal_feature_pack` | `np.empty` 跳过对未初始化 → 降序时间轴输出堆内存垃圾斜率（fail-open）；工具路径无升序校验 | **修复**：`np.full(NaN)`；`temporal_feature_pack` 对降序时间轴 typed 拒绝（与 `build_cube` 同红线）。红测：`test_descending_times_typed_refusal_not_garbage_slope`、`test_equal_timestamps_allowed_no_uninitialized_memory` |
| P1-1 | P1 | `rs_alignment` | 配对 ref 取排序首，可能携带声明缺口（cloud）资产而同刻存在有效观测；plan 配对表与槽位账口径矛盾 | **修复**：ref 序有效优先（gap_code None 在前）；descriptor 槽位去重键加入有效性维度（valid+gap 同槽位合法共存）。红测：`test_pair_prefers_valid_asset_over_declared_gap` |
| P1-2 | P1 | `recipe_packs/__init__` + `conformance.py` | PACK_MODULES 新域缺 conformance family → master 既有测试 `test_corpus_covers_all_pack_domains` 回归失败 | **修复**：新增 `rs-cube-joint-analysis` family（domain=rs_temporal_cube）；期望与真实 NL 路由对齐（task=temporal_trend；zh 短语避开 SAR 词头截胡）；分层样本用例 11→0 失败 |
| P2-1 | P2 | `align_acquisitions` | 合并描述符重建校验被单表 512 上限误拒（400+400 即炸），报 pydantic 裸错 | **修复**：合并走 `model_construct`（两输入各自已过 512 校验；跨模态 ref 撞车 typed 拒绝）。红测：`test_two_large_descriptors_align_beyond_single_cap` |
| P2-2 | P2 | `rs_cube_pipeline` | 可选样本通道 <4 样本时 `geographic_block_split` typed 拒绝炸掉全管线 | **修复**：`n_xy >= 4` 才 split；否则 split_report 表披露降级（`skipped_insufficient_samples`）。红测：`test_few_polygons_degrade_split_not_fail_pipeline` |
| P3-1 | P3 | `alignment.coverage` | 偶数对时 median 取上中位 | 修复：statistics.median |
| P3-2 | P3 | `rs_fusion` | 双侧零信号被判 consensus_negative | 修复：严格符号 + 新码 `BOTH_NEUTRAL=6`（码表只追加） |
| P3-3 | P3 | `build_coverage_card` | disclosures 无上界（naive 时刻 500 资产可灌 ~45KB） | 修复：首 16 条 + 计数截断 |
| P3-4 | P3 | `build_sample_matrix` | int 特征被判无效（JSON 通道常见） | 修复：int/float 接受、bool 排除 |
| P3-5 | P3 | `attach_polygon_samples` | 缺 coordinates 抛裸 KeyError | 修复：DegenerateData 带序号 |
| P3-6 | P3 | descriptor 槽位去重 | 以原始 time_iso 串为键，同刻异写绕过去重 | 修复：canonical epoch 为键（另加有效性维度，见 P1-1） |
| P3-7 | P3 | `rs_cube_tools._bounded_features` | 特征个数无上界（4M×N 组合） | 修复：`_TOOL_MAX_FEATURES=32` |

## 误报 / 核销的怀疑（reviewer 自己验证后排除）

- 并行 PR 边界：diff 对 `tools/__init__.py`、`modelops/**`、`capability_graph.py`、`conftest.py`、`main.py`、`evidence_claim`、`hotpath`、`mission_runtime`、`completion` 零命中。
- refs-only：`to_context_summary`/工具输出/管线 layers 全部有界摘要（样本 ≤16 资产、charts ≤32 点、fold 表 ≤256）。
- `parse_time_iso` 边界（"2024-03"、"+2024-03-01"、"2024-03-01T25:00"）全部 typed 拒绝；"20240301"/ISO 周日期确定性解析。
- 缺口平面诚实性：全 NaN 栈 → 100% nodata 码、零 valid；混合栈计数/比率手算锚点一致。
- 晚期融合权重：NaN/inf/负值/全零 → typed ValueError；inf 证据按缺源处理。
- 泄漏不变量对抗探测：聚类/重复坐标下 block→fold 仍是函数；时间前向链严格 `max(train_t) < min(test_t)`；同时刻样本永不跨折。
- SAR 一景一配：1 景 vs 3 期光学 → 恰 1 配对，确定性 tie-break。
- Kill-switch：`RS_TEMPORAL_CUBE=0` → RECIPES=[]（master 逐位一致）。
- 工具滥用面：10M 元素数组/畸形 descriptor/未知参数 → 结构化错误；无 path/eval 面。
- 契约引用：11 个 conformance node id 全部真实存在且通过；tool_candidates 与 capability 映射完整。

## 与最新 master / open PR 的交叉

- PR #1336（GeoAI platform 11）与 PR #1335（claim/mission fail-closed）：本分支零文件交集（见上）。
- 分支生命周期内 `origin/master` 未前进（faa453a8 不变，PR 前已再 fetch 复核）。
- 是否需要 integration PR：否——全部为 additive 注册（registry/skill/recipe pack），无 master 文件语义改写；`RS_TEMPORAL_CUBE=0` 可整体停用 recipe 面。

## 验证口径

- 新测试 116 全绿；`test_conformance_corpus.py` 10/10；capability parity 6/6；
  cartography 发布门 1125 绿+3 skip（R1 前基线，R1 后重跑相关面）；
  ruff（app+tests）全绿；`git diff --check origin/master...HEAD` 通过。
- 复核命令（连续两遍执行，结果一致）：
  `python -m pytest tests/unit/lib/test_rs_cube_descriptor_v1.py tests/unit/lib/test_rs_alignment_v1.py tests/unit/lib/test_rs_gaps_v1.py tests/unit/lib/test_rs_features_v1.py tests/unit/lib/test_rs_fusion_v1.py tests/unit/lib/test_rs_samples_v1.py tests/unit/lib/test_rs_pipeline_v1.py tests/unit/lib/test_rs_cube_wiring_v1.py -o addopts= -q`

## 剩余边界（记录，不阻塞）

- `sen_slope` 在 T>24 诚实跳过（NaN+披露）——O(T²·N) 无界版不做。
- CUSUM 变点无逐像元 bootstrap 显著性（披露；1D 序列的显著性走既有
  `temporal.changepoint`）。
- 布局语义（layover/shadow）是**声明语义消费**——像元级判识归
  `sar_v3.layover_shadow_mask`，本方向不重复实现。
- 工具面 `latency_class` 在 4M 值上界输入时实测 ~47s（reviewer 计时），
  与 medium 标称有差距——advisory metadata，不阻塞。
