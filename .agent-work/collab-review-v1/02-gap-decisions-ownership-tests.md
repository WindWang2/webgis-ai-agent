# GAP_ANALYSIS / DECISIONS / PARALLEL_OWNERSHIP / TEST_MATRIX（合并文档）

## GAP_ANALYSIS（Oracle → 现状差距）

| 能力 | 现状 | 差距 |
| --- | --- | --- |
| Proposal 契约 | 无 | ReviewProposal/Comment/Decision 契约 + 存储 |
| 锚定评论 | 无（"review"一词已被 lifecycle review/harness review 占用） | AnchoredComment（layer/component/feature/claim/artifact）+ stale 检测 |
| 状态机 | 无 | draft/submitted/changes_requested/approved/rejected/merged/superseded/withdrawn |
| 审批策略 | 无 reviewer/approver 帮手（角色仅 viewer/editor/admin；scope 词表封闭） | 服务层 per-proposal 策略：Agent 永不自批；高风险需 distinct human reviewer + role≥editor；匿名会话高风险 fail-closed 403 |
| 冲突/rebase | CAS 已有（superseded 语义），无 proposal 级 | base_revision 漂移检测 + 目标存在性 rebase 校验 |
| 事务合并 | apply_gis_mutation + checkpoint/rollback 已有 | 合并编排 = checkpoint → intents 链式 CAS 回放 → 失败回滚；禁止第二条 mutation 路径 |
| Audit evidence | record_audit（org 级 DB, fail-open）+ provenance ring | review store 内 append-only decision/merge journal（结构化，无 CoT/secret）+ 导出投影 |
| 通知 | bus 词表封闭 {doc,delta,presentation,op,presence,lock,artifact} | additive 新增 "review" kind（服务端 VALID_KINDS + 前端 protocol/adopt） |
| 前端 | drawers 模式（open/onClose+useDialogFocus）、collab store（useSyncExternalStore） | review drawer：列表/diff/锚定评论/approve/request changes/stale 徽标 |

## DECISIONS（ADR-0201 摘要）

1. **存储**：per-session 目录原子 JSON（与 checkpoint manifest 同款）——proposal 治理对象与其所治理的 mapspec（同为 Redis+盘）同生命周期；不引 DB 迁移（避让 #1355 的 0092 迁移号冲突）。审计导出 = store 投影。
2. **复用而非重建**：proposal mutation intents 复用 `mapspec_mutation_schema` 的 14 Body 模型（discriminated union）；merge 复用 `apply_gis_mutation`（origin="system", actor=review_merge:{pid}）+ Checkpoint/RollbackIntent。不建第二条写路径。
3. **身份/策略**：actor=(user_id, kind∈{user,agent}, role∈{anonymous,viewer,editor,admin})。策略纯函数：agent 作者 → 必须 distinct human 审批（任何风险）；user 高风险 → distinct reviewer role≥editor；匿名会话高风险 → 403 fail-closed；user 低风险 → 可自批（单人会话可用）。
4. **风险分级**（确定性）：high = remove_layer/remove_component/rebind_component/patch_workbench_state；其余 low。claim 锚定 + high → 同 high 路径（v1 不再加严到 admin，记录为边界）。
5. **事件**：bus 新增 "review" kind（≤2KB 载荷：proposal_id/event/status/actor/seq）；WS 透明转发；前端 protocol 白名单 additive。
6. **锚点 stale 语义**：layer/component 锚 → 当前 spec 缺失即 stale；claim 锚 → ClaimStore 可达且缺失即 stale，不可达 = unverified（诚实：未知≠stale）。
7. **术语**：限定词 "Map Review（评审/会签）"；全称 ReviewProposal / AnchoredComment / ReviewDecision / ApprovalPolicy；UBIQUITOUS_LANGUAGE 补条目并指明与 lifecycle review / harness review 的区别。
8. **ADR 编号 0201**（0197 已被 durable-mission-runtime 占用；0198–0200 在未合 PR 分支，PR 前复查）。

## PARALLEL_OWNERSHIP

- 本分支拥有：`app/services/review/**`、`app/api/routes/review_proposals.py`、
  `app/schemas/review_schema.py`、`tests/review/**`、`frontend/lib/review/**`、
  `frontend/components/workbench/review-drawer.tsx`、ADR-0201、
  `review/COLLABORATIVE_SPATIAL_REVIEW_APPROVAL_REVIEW.md`。
- 触碰的共享文件（冲突面）：`app/services/collab/bus.py`（+1 kind）、
  `app/main.py`（+1 router）、`frontend/lib/collab/protocol.ts`+`adopt.ts`（+1 kind）、
  `docs/api-docs.md`、`tests/quality/snapshots/openapi.json`、CHANGELOG、UBIQUITOUS_LANGUAGE、CONTEXT。
- 禁入：#1335（evidence_claim verify）、#1336 热区（modelops/geoai、embedding-cache、components/geoai）、
  #1353 的 workbenchSlice.ts、#1355 的 spatial_events/migrations、#1356 的 mapspec_store.py/lifecycle_engine.py（其 SetSceneIntent 与我无关——我不改 engine）。
- 与 #1355 的 openapi.json/docs/api-docs.md 快照冲突：PR 前 rebase 后重新生成。

## TEST_MATRIX

| 维度 | 用例（TDD 先行） |
| --- | --- |
| 契约 | proposal/comment/decision 序列化往返；malformed intent 拒绝；字段边界 |
| 状态机 | 全部合法/非法迁移矩阵；终态不可迁；withdraw 边界 |
| 策略 | agent 自批拒（fail-closed）；agent approval 不计数；高风险 distinct+role；匿名高风险 403；低风险自批允许 |
| 锚点 | layer/component/feature/claim 锚创建；层删除后 stale；claim 缺失 stale；store 不可达 unverified |
| 冲突/rebase | base 漂移 merge 拒（conflict）；rebase 目标存在性（目标已删 → 拒）；rebase 后 merge 成功 |
| 合并 | happy path 端到端（create→submit→comment→approve→merge 后 mapspec 断言）；CAS 链式；中途失败回滚（checkpoint 恢复）；重复 merge 幂等拒 |
| 并发 | 合并期间用户 mutation 竞争（superseded → conflict）；双 merge 竞争仅一胜 |
| 审计 | decision/merge journal 完整；导出无 owner_token/无 CoT 字段；不可变（append-only） |
| API | 路由 happy/404/403/409/422；auth 依赖接线 |
| 前端 | protocol 'review' kind 解析；store 状态迁移；drawer 渲染（vitest） |
| 兼容 | feature-off：不注册 router 时全站行为不变；旧会话无 review 目录 → 空列表 |
