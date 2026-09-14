# qc-loop Round 2（2026-09-13）

分支 `quality/review-optimize-loop`，起点 tag `qc-loop-r1`

## R · Review

- 扫描范围：`app/services/gis_harness/**` 全量 114 文件 / 43,714 行，8 个并行审查代理全文精读（分片 `round2-part{1..8}.txt`；平台并发限额导致分 4 波派发，每片独立完整读毕）。
- 产出：`round-2.pool.csv`，97 条候选（**P0=0 / P1=13 / P2=84**）。全部 13 条 P1 由主会话逐一读码复核（含写侧/读侧、调用点证据）。
- **假数据红线（P0）零命中**：gis_harness 无「返回假数据/占位数据」实现。测试孤儿/未接线的"已完成设计"较多（见下），但均如实披露、无伪装。
- 至此**任务书声明的全 scope 扫描完成**（cartography 63 + gis_harness 114 = 177 文件 / 67,773 行）。

## P · Prioritize（top-3，score = sev×blast/(cost×risk)，成本按诚实重估）

| id | 位置 | 类别 | 分数 | 落选说明 |
|---|---|---|---|---|
| R2Q011 | runtime_state_machine.py:295 | correctness P1 | 23.1 | — |
| R2Q001 | tool_surface.py:149 | correctness P1 | 20.2 | — |
| R2Q013 | workflow_instance.py:615 | correctness P1 | 18.1 | — |

高价值落选（round 3 输入）：map_critique.py:79 layers 形状错配致 5 项 V7 检查整体失效（fix 成本重估后 17.1）、planner.py:698 UnboundLocalError（17.3）、completion/pipeline.py:121 截断先于判定（16.1）、Q002 pdf_renderer transform（19.1，需完整含 golden 门禁轮）、Q023 component_registry fail-open（17.3）。

## F · Fix（每条独立 commit）

1. `dd82527f` fix(qc-loop): guard _task_complete against missing product_verdict key (round 2, correctness)
   - legacy map_product 块两键皆缺时 verdict=None → `.startswith` AttributeError 沿 derive_runtime_phase 传播、被生产调用点吞掉 → V7 状态机对 legacy 会话静默冻结。修复与同文件 `_verdict_ready` 的 `str(verdict or "")` 同口径。+回归测试 2 条。
2. `0953174f` fix(qc-loop): count available data rows as done in chapter phase derivation (round 2, correctness)
   - done 集合补 `"available"`（`_mark_progress` 写入的完成态，session_plan.py:522）。此前阶段永久卡死 PHASE_DATA，pi 路径每轮工具面据此编译 → `("core","statistics")` 等分析域工具永不激活。+回归测试 2 条。
3. `fc7f5382` fix(qc-loop): pass raw workflow_contract to gate fingerprint so skip gate works (round 2, correctness)
   - 写侧 `str(chapter.get("workflow_contract") or "")` 强转使 `gate_fingerprint` 的 `isinstance(contract, dict)` 永假、contract 指纹恒空；读侧（refresh 门比对）传原始 dict → contract 存在时两侧永不匹配，文档承诺的「不变即跳过」失效，每次工具结果/轮末/render 观察都全量重推+分布式锁+save_session_plan。修复后两侧同形（升级后首次触发多推一次，随后恢复幂等）。+回归测试 3 条。

无公共 API 语义变更（全部为异常路径/输入形状容错与内部一致性），免 ADR。

## V · Verify

- ruff：3 个变更文件 + 3 个新测试文件通过。
- 新回归测试 7 条全绿；受影响模块既有测试（tool_surface / runtime_state_machine_v7 / workflow_instance / workflow_compiler）81 条全绿。
- 门禁（SKIP_BROWSER=1，未触及导出/渲染）：`round-2.gate.log`。

## L · Log · 指标

| 指标 | round-1 末（open） | round-2 发现 | round-2 修后（open） |
|---|---|---|---|
| P0 | 0 | 0 | **0** |
| P1 | 4 | +13 | **14** |
| P2 | 58 | +84 | **142** |

Δ(字面公式) = (0−0)×3 + (4−14)×1 + (58−142)×0.3 = **−35.2**。为**发现爆发轮**的一次性效应：本轮首度扫完 gis_harness，发现（10 P1 + 84 P2 入池）远超修复（3 P1）。E2（连续 2 轮 Δ<1.0）自 round 3 起判定——池已封闭，Δ 将反映纯修复改善。棘轮：无旧缺陷回退、无回归引入（既有测试全绿 + 门禁判定见 gate 日志）。

## 遗留（Round 3 输入）

- open P1 14 条，按分数头部：Q002（pdf_renderer，需含 golden 完整门禁）、R2-6 planner candidates（17.3）、Q023 component_registry fail-open（17.3）、R2-2 map_critique 形状错配（17.1）、R2-3 recipes 资格门键名（16.1）、R2-10 pipeline 截断（16.1）、Q037 render_scene 舍入 parity（14.4，需 golden）……全量见两份 pool.csv（status=open）。
- 扫描游标已到 177/177；后续轮为纯修复轮（不再有发现爆发）。
