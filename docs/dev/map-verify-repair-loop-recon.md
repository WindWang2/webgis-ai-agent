# Recon — 方向 5：Map Verification → Critique → Repair 闭环（2026-09-20）

基准：`origin/master` = `5a4d4632`（PR #1478）。分支：`feat/map-verify-repair-loop`。

## 1. 现状：闭环已合并的部分（不得重复实现）

闭环 `Render → Observe → Verify → Findings → Repair → Re-render → Re-verify → Complete`
在 V3–V7 波次（ADR-0081/0086/0088/0104/0118/0119/0134/0183）已分阶段落地，
**四个既有环**，各自有界、有停止条件：

| 环 | 位置 | 修复面 | 预算/停止 |
|---|---|---|---|
| 制图 quality loop（desired-state 呈现） | `app/lib/cartography/quality_loop.py` | palette/opacity/legend/layout 钳回（6 op 白名单） | `MAX_REPAIR_ITERATIONS=2`；`repeated_failure`/`repeated_patch` 指纹停；user-wins suppression；锁 guard；superseded 代次停 |
| runtime repair（desired 对、runtime 偏） | `app/services/gis_harness/runtime_repair.py` | reassert layer/component、restore visibility | `MAX_RUNTIME_REPAIR_PASSES=2`；ledger 按 spec fingerprint 分代；user-owned no-op |
| Map Product Finalizer | `app/services/gis_harness/completion/` | add/enable component、show layer（走既有突变通道） | `MAX_FINALIZATION_PASSES=2`；repair_memory one-shot；user_removed 不复活；幂等门（revision+rows fp+render seq）；final_gate 强制重验 |
| repair planner（分类+防循环账本） | `app/services/gis_harness/repair_planner.py` | 只分类/计划/护栏（16 repair_class × 5 safety） | W11 账本 `map_state[_repair_loop_v6]`；同 epoch 重复 → `no_progress`；≥3 → `repair_exhausted` |

完成语义（V5）：`derive_product_verdict`（READY*/NEEDS_REPAIR/BLOCKED_BY_DATA/METHOD）
+ 七维 completion contract + `final_map_status` + `task_complete` 折叠 +
goal satisfaction（ADR-0183，fail-closed）+ intent acceptance + display confirmation
（auto/required seam）+ claim ingest。触发点：`agent_pi_bridge.py:895/2537`、
`chat.py:941/1937`（tool_result / turn_settled / observation POST）。

## 2. 真实缺口（本方向工作包）

- **G1（V1）UnifiedFinding 契约不完整**：`unified_findings.py` 缺
  finding id、**finding class 轴**（semantic/gis_correctness/cartographic/
  visual/runtime_display/export —— 现有 domain 是生产者轴不是类别轴）、
  user-ownership 标志、recurrence fingerprint（repair_planner 里
  `finding_fingerprint` 私有重算，finding 本体不携带）。
- **G2（V2）统一投影只覆盖 3/5 域**：`collect_unified_findings` 不收
  cartographic review checks（`semantic_check` 域声明“预留 W8”从未接线）
  也不收 visual findings → 制图层的 fail 规则从不进 repair planner 的
  分类/账本面，「finding vocabulary 统一」未达成。
- **G3（V4）finalizer 环内无 recurrence/no-progress 硬停**：
  `run_map_finalization` while 循环只靠 `max_passes=2` 与“修复返回空”
  兜底；同一 finding 指纹在修复后被再次报出且同修复再次可申请时，
  会重复对抗到轮数上限（W11 账本只作用于披露面 repair_plan，不反哺
  环内修复决策）。
- **G4（V2 L4）visual seam 无生产调用方**：`visual_evaluator.py` m1 注记
  明示“接线是独立 roadmap 项”。finalization/visual_repair 触发点、
  snapshot 组装、findings 入统一披露全部缺席（默认关闭语义保留）。

## 3. 禁做清单（防重复/防平行体系）

- 不建第二套 MapSpec / 第二 finding 类型 / 第三修复通道
  （quality_loop、runtime_repair、finalizer 三环是权威执行面）；
- 不迁移各 domain 词表（红线：只投影不新造码）；
- 不把 LLM/视觉评估当唯一 verifier（visual findings 恒 degradation_only）；
- repair 不重跑 GIS 分析（`needs_execution` 披露交还 DAG）；
- 不动 completion 状态机语义（STATUS_*/VERDICT_* 冻结词表）；
- 不碰 i18n / 审计杂项（#1436、#1377 在其他分支）。

## 4. 文件热区

- `app/services/gis_harness/completion/unified_findings.py`（G1/G2 主场）
- `app/services/gis_harness/completion/pipeline.py`（G3 循环 + G4 触发点）
- `app/services/gis_harness/repair_planner.py`（复用其 fingerprint/账本，不重写）
- `app/services/gis_harness/visual_evaluator.py`（G4 seam 消费）
- 测试：`tests/unit/gis_harness/test_unified_findings_v6.py`、
  `test_repair_planner_v6.py`、`test_map_completion.py`、`test_map_critique_v7.py`

## 5. 决策：工作包映射

| 缺口 | 工作包 | 原则 |
|---|---|---|
| G1 | W-A：UnifiedFinding + finding_id/finding_class/user_owned/recurrence_fingerprint（additive，to_dict 有界） | 单一推导点、旧读者零漂移 |
| G2 | W-B：`from_cartographic_check` + `from_visual_finding` 投影器；collector 扩参；plan_repairs_for_chapter 传入 `_cartographic_review` | 词表原地保留只投影 |
| G3 | W-C：finalizer 环内 per-finding 指纹 → 同指纹+同修复复现即停（no_progress 披露，status 如实 needs_repair） | 有界、不对抗 |
| G4 | W-D：finalization/visual_repair 触发点 + snapshot（有界、ref 化）+ findings 入 collector（degradation_only） | 缺席=零行为变化 |
