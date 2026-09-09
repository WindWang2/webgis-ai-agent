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
- [ ] W3 Artifact/MapSpec/Node lineage 双向索引
- [ ] W4 Semantic Diff→Affected Subgraph 接线
- [ ] W5 Partial Recompute + Reuse Validation
- [ ] W6 Unified Findings adapter
- [ ] W7 Completion Verdict 单一化
- [ ] W8 Deterministic Cartographic Observation
- [ ] W9 Visual Observation seam
- [ ] W10 Repair Planner
- [ ] W11 Repair Loop 防循环
- [ ] W12 Tool Retrieval V6 + 语料 ≥300
- [ ] W13 Contextual Context Assembly
- [ ] W14 Resume VNext
- [ ] W15 Human-Agent 状态收敛（锁下沉）
- [ ] W16 Closed-loop Corpus ≥100 + 10 E2E
- [ ] W17 Performance/Security
- [ ] W18 Docs/ADR/CHANGELOG

## Review / 收尾
- [ ] Review Round 1（Lens A/B，BLOCKER/CRITICAL/MAJOR 清零）
- [ ] Review Round 2（perf/concurrency/security/seam）
- [ ] Claim Honesty Review
- [ ] rebase origin/master + 关键测试复跑
- [ ] 推送 + PR（按 §64 模板）

## Definition of Done 对照（§60，逐项核对见最终 11-pr-summary.md）
最新 master 审计 ✅（2026-09-09）；其余 33 项随 waves 推进更新。

## 日志
- 2026-09-09：Goal 启动；worktree 就绪；Phase 0 完成；顺带修复 kimi-code subagent 通道（opencode-zen provider 补 x-opencode-session/User-Agent 头 + muse-spark support_efforts/default_effort=high + secondary_model.default_effort=high）。
- 2026-09-09：W1+W2 完成。`runtime_bridge.py`（初置于 workflow_v4/ 后因该包「零 I/O」红线迁至 gis_harness/ 根）：typed node_id 成共享命名空间，节点状态 = plan_graph 同一派生源在 typed DAG 上的投影；证据指纹覆盖 capability 全部行（修首行失明）；output 节点证据继承产出者（仅 upstream_stale）。12 单测 + gis_harness 域 998 全绿；plan_orchestrator 相关 48 全绿；ruff 净。
