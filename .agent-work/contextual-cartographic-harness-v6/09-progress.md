# 09 — Progress 跟踪

图例：`[ ]` 未开始 `/` 进行中 `[x]` 完成（附 commit）

## Phase 0：审计
- [x] fetch + ff 确认 master 最新（HEAD 8a33e3a5）
- [x] worktree + branch 建立（feat/contextual-cartographic-harness-v6）
- [x] 基线测试绿（cartography 门 708 passed）
- [x] 双路审计 + 主 agent 二次验证 → 00-baseline.md
- [x] 12 份审计/设计文档建立

## Waves
- [x] W1 Canonical Workflow Runtime Projection（`app/services/gis_harness/runtime_bridge.py`：`derive_runtime_block` 纯投影 + `workflow_runtime_v6` 单键；StageState 词汇复用；证据漂移→typed 边下游闭包 stale）
- [x] W2 Compiler→Runtime bridge（服务入口 `maybe_update_runtime_projection` 接入 3 触发点：agent_pi_bridge 成功/失败、chat observation 路由；LLM make_plan 路径补齐 V4 证据消灭不对称；kill switch `GIS_WORKFLOW_RUNTIME_V6`）
- [x] W3 Artifact/MapSpec/Node lineage 双向索引（runtime 块 `artifact_index`：ref → producer_node/consumer_nodes/layer_ids/component_ids；节点 `inputs` 输入血缘；`_cap_all_refs` 全行登记防过渡态失明；查询 API `artifact_lineage`/`node_lineage`；服务入口接 `mapspec_store.get_mapspec`）
- [x] W4 Semantic Diff→Affected Subgraph 接线（字段级变更分类 algorithm/parameter/data → `compute_affected_subgraph` 成唯一闭包引擎；`changes`+`recompute_plan` 进运行态块；`[GIS Recompute]` 行进 SessionPlan 投影=调度面；修复 bounded 边 port 后缀断链缺陷 + 回归锁）
- [x] W5 Partial Recompute + Reuse Validation（`records` 快照进 derive；artifact health/package 稳定/evidence 三校验 → safe/unknown/unsafe；unsafe 翻 stale 强制重算 `reuse_unsafe:*`；style-only/revision 推进结构性免疫科学重算专测）
- [x] W6 Unified Findings adapter（`completion/unified_findings.py`：UnifiedFinding 12 字段投影；domain=harness_finalizer/render_diagnostic/workflow_runtime；blocks_completion 单点推导 `_blocks` + stale 显式例外；`collect_unified_findings` 确定性序 + 有界）
- [x] W7 Completion Verdict 单一化（`evaluate_completion_contract` analysis 维纳入 runtime stale 硬输入；`derive_product_verdict` READY* 遇 stale 压 NEEDS_REPAIR；无运行态块旧章节 parity 零漂移专测）
- [x] W8 Deterministic Cartographic Observation（floating 组件实测 rect 重叠/完全越出画布 → layout_conflict warning 进主校验链；`derive_component_lifecycle` 统一组件生命周期投影 requested→…→diagnostics；前端 observation 增 `canvas` 容器像素遥测 + DTO 白名单；旧客户端门控零误伤）
- [x] W9 Visual Observation seam（`visual_evaluator.py`：触发白名单 §40；`GIS_VISUAL_EVALUATOR` hook 默认关闭；输出白名单校验——mutation 意图/非形状条目结构性判废，强制 domain=visual + degradation_only + 不硬阻断；评估器不接触 MapSpec）
- [x] W10 Repair Planner（`repair_planner.py`：UnifiedFinding → 16 修复类 × 5 安全级表驱动分类；code 精确→scope 兜底；锁/override → not_allowed（user-wins 硬约束）；visual 软发现一律 requires_user_approval；degradation 面不产生自动动作；executor 只列既有通道——不建第三修复通道；`plan_repairs_for_chapter` 接入 maybe_finalize_map_product，repair_plan 进 map_product 块）
- [x] W11 Repair Loop 防循环（finding 指纹 + state epoch（runtime_rev:mapspec_rev）+ 尝试计数账本 map_state[_repair_loop_v6]；同 finding 同 epoch → no_progress；≥3 次 → repair_exhausted → abort_with_disclosure 披露）
- [x] W12 Tool Retrieval V6 + 语料 ≥300（`7a94c4e3`：ToolSemanticIndex 生产实现＋kill-switch＋paraphrase 240 条→306 条，PINNED_* 未动全绿，33 passed）
- [x] W13 Contextual Context Assembly（`0f31a466`：v6_context_blocks 三层投影＋字节 hard cap＋度量进 budget_report，既有块通道注入，19 新＋相关 142＋上下游 159 passed）
- [x] W14 Resume VNext（`1e25a308`：resume_verify 三裁决＋anchor schema v2＋Scenario 9 五节点链中断恢复全绿）
- [x] W15 Human-Agent 状态收敛（`8f567075`：guard_locked_partitions 统一 guard＋lockedComponentIds＋override 三分类＋transient 剥离，Scenario 8 三路径全绿）
- [x] W16 Closed-loop Corpus ≥100 + 10 E2E（`3a8bfa6f`：corpus 17×12=204 条，六段式期望零自创词汇；§57 S1-S10 一文件十测全绿，17 新＋关联 82 passed）
- [x] W17 Performance/Security（`a8da6d6e`：perf 结构契约 7 项零 wall-clock＋安全门 5 项，12 项全绿，未动生产代码）
- [x] W18 Docs/ADR/CHANGELOG（本 wave 只写文档：docs/adr/0119-contextual-cartographic-harness-v6.md＋CHANGELOG harness-v6 条目＋11-pr-summary DoD 34 项＋本文件 Waves 行；不 commit）

## Review / 收尾
- [ ] Review Round 1（Lens A/B，BLOCKER/CRITICAL/MAJOR 清零）
- [ ] Review Round 2（perf/concurrency/security/seam）
- [ ] Claim Honesty Review
- [ ] rebase origin/master + 关键测试复跑
- [ ] 推送 + PR（按 §64 模板）

## Definition of Done 对照（§60，逐项核对见最终 11-pr-summary.md）
W1–W18 ✅（W18 只写文档）；待办 6 项：两轮 review / honesty / rebase+复跑 / 推送 / PR。

## 日志
- 2026-09-09：Goal 启动；worktree 就绪；Phase 0 完成；顺带修复 kimi-code subagent 通道（opencode-zen provider 补 x-opencode-session/User-Agent 头 + muse-spark support_efforts/default_effort=high + secondary_model.default_effort=high）。
- 2026-09-09：W1+W2 完成。`runtime_bridge.py`（初置于 workflow_v4/ 后因该包「零 I/O」红线迁至 gis_harness/ 根）：typed node_id 成共享命名空间，节点状态 = plan_graph 同一派生源在 typed DAG 上的投影；证据指纹覆盖 capability 全部行（修首行失明）；output 节点证据继承产出者（仅 upstream_stale）。12 单测 + gis_harness 域 998 全绿；plan_orchestrator 相关 48 全绿；ruff 净。
