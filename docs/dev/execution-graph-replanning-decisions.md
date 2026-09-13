# Execution Graph + Incremental Replanning — Decision Log（方向 5）

每个决策记录：背景事实 → 选项 → 裁决 → 理由 → 回滚面。编号 D1..Dn 按时间追加。

---

## D1 — ExecutionGraph 收敛对象：演进 V5 运行时，不造第五套 DAG

- **事实**：仓库已有四层图抽象（plan_graph 投影 / workflow_v4 typed DAG / workflow_runtime V5 可执行账本 / runtime_bridge 运行态块），且 V5 已具备任务书 E3/E4 要求的几乎全部执行语义（两级 CAS、run/node 租约、孤儿复位、取消、deadline、有界并发、STALE 拓扑安全重入队、复用指纹）。
- **选项**：(a) 新建 ExecutionGraph IR + executor；(b) 演进 V5 + 打通 chat 生产断链；(c) 只增强建议性投影。
- **裁决**：(b)。
- **理由**：(a) 直接违反"防重复施工"与"不要另造通用 Agent"；(c) 不解决"RecomputePlan 只算给人看"的历史断链（runtime_bridge.py:770-776 自述）。(b) 把增量重算从 REST-only 变成 chat 生产路径的一等事实。
- **回滚面**：所有新模块独立成包/文件；hook 调用点全部 fail-open try/except；`GIS_INTENT_DIFF_REPLAN=0` 一键关停。

## D2 — 变更源：master 现有 chapter 结构化 diff 优先，situation（#1275）作未来升级

- **事实**：#1275（gis_situation + diff）仍 open；master 上 follow-up 语义只有 `followup.classify_followup` 关键词五分类 + `goal_key`（scope|subject|task）等值比较。
- **裁决**：本任务在 master 语义上构建 `chapter_intent_diff`（旧/新 gis_chapter 的 intent 字段级对比 + capability 行签名对比）→ RECOMPUTE_DIMENSIONS 词表的 WorkflowChange/PendingChange。#1275 合并后可经同一 adapter 口升级为 situation diff 源。
- **理由**：不依赖未合并 PR；字段（intent.scope/subject/task、行 resolved_algorithm/params/bound_ref）在 master 稳定存在。

## D3 — 最小失效的落点：`webgis_map_intent` replace 分支保留完成事实 + apply_changes

- **事实**：同 goal_key 替换今天 void 全部行（session_plan.py:690-698）；异 goal_key supersede 归档全量。
- **裁决**：
  1. replace 分支：对行签名**未变**的 capability 保留 complete 行状态（走 `_mark_progress` 既有语义，不新增写手）；变化的行按变更维失效。同时把 PendingChanges 经 `ChangeApplier.apply` 写入本会话 V5 实例（STALE 最小集）。
  2. supersede 分支（异 goal）：保持现有全量语义（不同产品不复用），但新实例经既有 `_prefill_role_bindings` + ReuseIndex 跨实例复用兜底（已有，不重造）。
- **理由**：满足验收场景 2/3/8（"只看主城区"/"换成高中"/pin layer 后 replan）的最小重算要求；行状态词表单一（ProgressStatus），新写入点全部锁内（master 本存在 _mark_progress 之外的写手 —— review P2-1 更正，纪律是"锁内 + 同词表 + 同语义"而非字面第一写手）。
- **回滚面**：feature flag 关停后行为与 master 完全一致（全 void）。

## D4 — 副作用纪律：节点级 `side_effect` 词表 + 双通道执行语义

- **事实**：V5 契约/driver 无副作用分类（grep 证实）；adapters 全部 `idempotent=True`（geocompute 通道本身幂等）。
- **裁决**：typed DAG 节点 additive 字段 `side_effect ∈ {pure, derived_external, destructive}`（默认 pure）。规则：
  - pure：at-least-once，可自动重试（现状）；
  - derived_external：可重试但必须带 receipt（bound_ref 存在才可 SUCCEEDED）；
  - destructive：at-most-once，重试需显式指令（chat 确认），绝不自动 STALE 重算——STALE 只标披露。
- **理由**：任务书 E3 硬性要求；当前Pi 工具通道天然是 receipt 通道（geojson_ref）。
- **回滚面**：字段默认 pure → 全部现状行为。

## D5 — 图事件：复用 chat SSE，`workflow_graph` 事件族

- **事实**：现有通道 = `cache_session_plan_sse`（pi bridge 工具回调 → SSE 适配器）+ `event_resume` 断线续传；ws_service 是地图数据 WebSocket（另一用途）。
- **裁决**：新增事件族 `workflow_graph`（payload: instance_id/graph rev/node 状态变化数组/recompute decision 摘要），在 hook 链（工具结果后 + apply_changes 后）派发；**不建 websocket、不做逐 transition 流**（DB 账本是事实源，SSE 是有界投影）。
- **理由**：任务书 E8 要求"复用既有 SSE"；逐转移流会放大 DB 轮询压力。

## D6 — 并发：不新增第二套并发治理

- **事实**：#1279（governor）在 open PR 中做 admission/backpressure/budget；driver 已有 max_concurrency=4 有界并发；Pi 工具通道串行。
- **裁决**：本任务不建新的并发原语；与 governor 的接口点 = driver 的 dispatcher 装配（已有 env 装配缝），PR body 记录。
- **理由**：防平行系统。

## D7 — ADR 编号：取未被占用的 0184（0180-0182 已被 open PR 争用）

- **事实**：master 最高 ADR = 0179；open PR 占用：0180（#1274/#1275/#1277 三个 PR）、0181（#1276）、0182（#1278/#1279）。
- **裁决**：本任务 ADR = `0184-execution-graph-incremental-replan.md`（0183 留空隙防 #1273 后续占用；不回填他人空隙）。

## D8 — 测试与资源纪律

- 迭代期 focused tests `--no-cov -p no:cacheprovider`；后端并发 ≤ `-n 2`（无 xdist 则串行）；DB 路径测试沿用仓库 fixture；`next build` 仅最终 1 次。
- master 预存失败对照：任何全量回归失败先在干净 origin/master worktree 复跑归因，不掩盖。

（后续决策按 D9.. 追加）

## D9 — review P2 处置记录（Subagent B 终审，2026-09-14）

终审结论：PR-go，无 P0/P1，8 个 P2。处置：

- **已修（in-PR，review-fixes commit）**：
  - P2-1 「唯一写手」表述更正（ADR D3 / decisions D3 / recon §6：锁内+同词表纪律）；
  - P2-2 apply_intent_facts 节点变更总量 ≤MAX_APPLY_CHANGES 确定性截断（替代整批丢弃）；
  - P2-3 carried 节点现值 STALE → 跳过并披露（carried_skipped_stale），旧 ref 不洗白 STALE；
  - P2-4 删除无生产调用方的 node_states_event（节点粒度由 replanned payload 承载，逐转移刷流有意不做）；
  - P2-5 语料第 7 情境替换为 params_tweak（真实部分携带 0.833），指标按 kind 诚实分列；
  - P2-6 新增 EventKind.SIDE_EFFECT_NO_AUTO_RETRY（区别于预算耗尽语义）；
  - P2-7 canonical_params 深度封顶（>12 repr 化，防递归炸栈降级过保守）。
- **不修（后续项，可验证理由）**：
  - P2-8 coarse 维判定（algorithm>parameter>data）在 intent_diff / runtime_bridge / workflow_v4.diff 三处并存 —— 收敛需动 runtime_bridge 既有契约（跨线冲突面，#1273/#1277 在改），本 PR 记录为 follow-up；三处当前由测试分别钉住，漂移风险受控。
  - P2-5 遗留的 25 个身份契约用例保留（同章重提/呈现态变更零重算是需要钉住的契约本身，非冗余）。
