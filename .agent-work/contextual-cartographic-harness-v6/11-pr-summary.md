# 11 — PR Summary（最终 PR 内容草稿，随 waves 更新）

> PR 标题：`feat(harness): Contextual Cartographic Harness V6 — unified workflow runtime, visual observation, repair loop & partial recompute`
> 按 Prompt §64 必含小节逐节填写；完成前本文件为工作草稿。

## Problem / Motivation

（骨架）多系统平行：Compiler V4 DAG 只读咨询、runtime 双轨、partial recompute 未闭环、视觉观测缺位、锁 guard 前端单点。V6 收敛为唯一状态驱动、可观察、可诊断、可恢复、可局部重算的 Contextual Cartographic Harness。

## Baseline audit
→ 00-baseline.md（HEAD 8a33e3a5，file:line 证据）

## Architecture
→ 01-architecture.md

## Runtime unification / Typed DAG integration
→ 02-runtime-unification.md
- W1+W2 `13af748f`：`runtime_bridge.py` + 3 触发点接线 + LLM 路径 V4 证据补齐
- W3 `35c7bbbb`：artifact_index 双向 lineage + 查询 API

## Partial recompute / Artifact lineage
→ 05-recompute-model.md
- W4+W5 `81686755`：变更分类→唯一闭包引擎→reuse validation 三校验

## Visual observation / Unified findings / Repair planner
→ 03-visual-observation.md / 04-unified-findings.md
- W6+W7 `cdf8ebce`：UnifiedFinding 12 字段投影 + Verdict 单一化
- W8+W9 `529e869f`：确定性制图观测 + Visual Evaluation seam
- W10+W11 `3662a07f`：Repair Planner 16×5 + 防循环护栏

## Tool retrieval / Context / Resume
→ 06-tool-retrieval.md / 07-context-state.md
- W12 `7a94c4e3`：ToolSemanticIndex + 语料 66→306
- W13 `0f31a466`：三层投影 + 字节 hard cap
- W14 `1e25a308`：resume_verify 三裁决 + anchor schema v2
- W15 `8f567075`：锁下沉 + 状态三分类（Scenario 8）
- W16 `3a8bfa6f`：closed-loop corpus 204 + §57 十场景 E2E
- W17 `a8da6d6e`：perf 7 + 安全 5（未动生产代码）
- W18（本文档 wave，只写文档，不 commit）：ADR-0119 + CHANGELOG + 本文件 + 09-progress

## Key code paths / API changes / DB changes / UI changes
- 新增：`app/services/gis_harness/runtime_bridge.py`、`completion/unified_findings.py`、
  `visual_evaluator.py`、`repair_planner.py`、`resume_verify.py`、
  `app/services/chat/tool_semantic_retrieval.py`、`v6_context_blocks.py`、
  `app/evaluation/closed_loop_corpus.py`；测试 12 文件（unit + perf）。
- DB：零新表——runtime projection 进 gis_chapter additive 键（若必须新表基于 head `0033_geocompute_v6_cluster`）。
- UI：observation 增 canvas 像素遥测 + DTO 白名单；workbench 增 lockedComponentIds。

## Security / Performance / Test matrix
- cartography 门 708 passed；gis_harness 域 1035 全绿；ruff 净；tsc 净。
- 语义语料 306（PINNED_* 全绿）；闭环语料 204；E2E S1–S10 全绿。
- perf 结构契约 7 + 安全门 5，共 12 项全绿（`a8da6d6e`）。

## Definition of Done（§60，共 34 项）

- [x] D01 最新 master 审计（2026-09-09，HEAD 8a33e3a5 → 00-baseline.md）
- [x] D02 W1 Canonical Runtime Projection（`13af748f`）
- [x] D03 W2 Compiler→Runtime bridge（`13af748f`，同 commit）
- [x] D04 W3 Artifact lineage 双向索引（`35c7bbbb`，15 新例 / 域 1001）
- [x] D05 W4 Diff→Affected Subgraph 接线（`81686755`，bridge 21 + semantics 20）
- [x] D06 W5 Partial Recompute + Reuse Validation（`81686755`，同 commit）
- [x] D07 W6 Unified Findings adapter（`cdf8ebce`，9 新例 / 域 1017）
- [x] D08 W7 Completion Verdict 单一化（`cdf8ebce`，同 commit）
- [x] D09 W8 Deterministic Cartographic Observation（`529e869f`，11 新例 / 域 1028）
- [x] D10 W9 Visual Observation seam（`529e869f`，同 commit）
- [x] D11 W10 Repair Planner（`3662a07f`，7 新例 / 域 1035）
- [x] D12 W11 Repair Loop 防循环（`3662a07f`，同 commit）
- [x] D13 W12 Tool Retrieval V6 + 语料 ≥300（`7a94c4e3`，306 条 / 33 passed）
- [x] D14 W13 Contextual Context Assembly（`0f31a466`，19 + 142 + 159）
- [x] D15 W14 Resume VNext（`1e25a308`，7 新 + 回归 51）
- [x] D16 W15 Human-Agent 状态收敛（`8f567075`，15 新 + 回归 1065）
- [x] D17 W16 Closed-loop Corpus ≥100 + 10 E2E（`3a8bfa6f`，204 条 / 17 + 82）
- [x] D18 W17 Performance/Security（`a8da6d6e`，perf 7 + 安全 5）
- [x] D19 W18 Docs/ADR/CHANGELOG（本 wave：ADR-0119 + CHANGELOG harness-v6 + 本文件 + 09-progress，只写文档）
- [x] D20 cartography 门 708 passed 保持绿
- [x] D21 gis_harness 域全绿（W11 时点 1035）
- [x] D22 ruff / tsc / eslint 净
- [x] D23 findings 棘轮零对齐（新 code 上限 0）
- [x] D24 语义语料 ≥300（306，PINNED_* 全绿）
- [x] D25 闭环语料 ≥100（204，零自创词汇）
- [x] D26 E2E S1–S10 全绿
- [x] D27 perf 结构契约全绿（7 项，零 wall-clock）
- [x] D28 安全门全绿（5 项）
- [ ] D29 Review Round 1（Lens A/B，BLOCKER/CRITICAL/MAJOR 清零）——待办
- [ ] D30 Review Round 2（perf/concurrency/security/seam）——待办
- [ ] D31 Claim Honesty Review——待办
- [ ] D32 rebase origin/master + 关键测试复跑——待办
- [ ] D33 推送——待办
- [ ] D34 PR（按 §64 模板）——待办

## Review round 1 / 2
→ 10-review-findings.md（待两轮 review 后填）

## Rebase verification / Backward compatibility / Known limitations
（收尾填；兼容：四 kill-switch 默认语义零漂移——GIS_WORKFLOW_RUNTIME_V6 /
GIS_TOOL_RETRIEVAL_V4 / GIS_TOOL_SEMANTIC+TOOL_RETRIEVAL_SEMANTIC /
GIS_VISUAL_EVALUATOR）
