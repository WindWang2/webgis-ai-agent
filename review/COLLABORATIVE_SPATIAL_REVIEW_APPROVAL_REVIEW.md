# 独立对抗性 Review — COLLABORATIVE_SPATIAL_REVIEW_APPROVAL（ADR-0201）

- Reviewer：独立 Subagent（general-purpose，只读 + 临时探针，探针已删）
- 被审对象：`collab/spatial-review-approval-v1` @ `ed603a38`（基线 `origin/master` = `faa453a8`）
- 结论：**无 P0**；3×P1（2 个当场复现、1 个结构性论证）+ 3×P2 FIX-FIRST → **本分支已在后续 commit 全部修复**（P1 全数 + merge 临界区相关的 P2；剩余 P2/P3 记录见下）

## 复现 → 红测试 → 修复 → 回归（P0/P1 强制流）

### P1-1 合并回滚摧毁"回滚前间隙"里的并发提交（复现 ✔）

- 现象：引擎锁逐笔；intent `is_error` 后、`RollbackIntent` 执行前的 await 间隙允许并发提交落库；旧实现回滚不带 CAS，把并发方工作连同半合并态一起清掉，且 evidence 记 `rolled_back=True, interleaved=False`（把破坏伪装成干净回滚）。
- 红测试：`tests/review/test_review_review_fixes.py::test_p1_rollback_aborts_when_concurrent_commit_lands`（spy 在 RollbackIntent 时注入并发 set_view）。
- 修复（`app/services/review/merge.py`）：回滚带 `expected_revision=rev`（CAS）；回滚 superseded → `interleaved=True, rolled_back=False, failure=…+interleaved_before_rollback`；回滚自身失败 → `+rollback_failed` 大声存证。
- 回归：78/78 绿。

### P1-1b（P1，结构性）ReviewStore 跨进程丢失更新

- 现象：`proposals.json` 整文件读改写只有进程内 asyncio.Lock；多 worker 部署下 last-writer-wins —— A 进程记的批准可被 B 进程的评论抹掉，终态可回退。mutation 平面自身用 `session_lock_registry`（Redis 跨 pod 互斥，生产）。
- 修复（`app/services/review/store.py`）：`save_proposal`/`mutate_proposal` 外层包 `session_lock_registry.lock(session_id, fail_on_degraded=True, fail_on_lost=True)`（与引擎同源同语义；降级/丢失 fail-closed）。锁 spy 红测试：`test_p1_store_mutations_go_through_session_lock_registry`。
- 备注：与引擎在测试环境同走进程内锁（conftest 钉 USE_REDIS=false），生产行为等价引擎。

### P1-2 decision 上界只在读时强制 → 201 条决策 brick 整个会话审查面（复现 ✔）

- 现象：`add_comment` 写时限 500，`record_decision` 无写时上界；schema 校验只在**加载**时跑 → 第 201 条决策被接受并持久化，此后所有读抛 `ReviewStoreCorrupt` → 会话全部审查端点（含审计导出）500。
- 红测试：`test_p1_decision_bound_enforced_at_write`（monkeypatch 上界=5）。
- 修复（`app/services/review/service.py`）：`record_decision` 锁内写时校验 `MAX_DECISIONS_PER_PROPOSAL`（经模块属性读取，可测），超界 `InvalidTransition(409)`；存储保持可读。

### P2-1 merge 与 reject/withdraw/rebase 竞态：变更落地却无 proposal 侧存证

- 修复（`app/services/review/service.py`）：merge 前锁内 test-and-set `merge_in_progress`（新 proposal 布尔闸，非状态机成员——中止须无痕）；replay 期间 `submit/record_decision/rebase/withdraw/supersede` 一律 409；引擎异常 `BaseException` 路径清闸不留永久排斥；成功/中止/双-merge 三条路径都持久化 merge_evidence 并清闸。
- 红测试：`test_p2_merge_race_aborts_and_persists_evidence`（spy 中途 withdraw → 被拒）。

### P2-2 失败/交错合并的 evidence 只活在 HTTP 回执

- 修复：`outcome.ok=False` 路径（含 base 漂移冲突）把 `merge_evidence` 落库。红测试：`test_p2_merge_failure_evidence_persisted`。

### P2-3 无漂移 rebase 不使旧批准过期（文档与实现不一致）

- 修复：`rebase` 在 `new_base == base` 时显式 409（无漂移 rebase 无意义；UI 本就只在冲突时提供）。红测试：`test_p2_rebase_without_drift_rejected`。ADR-0201 §2 决策四/§3 已同步。

## 误报澄清

- 无。Reviewer 的 9 项 findings 全部核实为真（3 P1 + 3 P2 + 3 P3）；另 1 项 P3（feature-off 404 在所有权依赖之后）为真但按接受边界记录（无数据泄漏）。

## 剩余 P3（接受并记录，post-merge 可加固）

1. `changes_requested` 期间记录的 approve 在同 base resubmit 后继续计数（intents 不可变、 APPROVED 翻转仍需新 approve 事件——语义噪音非旁路）。
2. `mutation_ids` 语义：只含**已落地** intent 的幂等键（本轮已把 superseded 剔除），checkpoint/rollback 用引擎自铸 id 不入该列（`applied[]` 为落地真相）。
3. store 空 session_id 拒绝已补（防御纵深；HTTP 路径参数天然非空）。
4. merge 对 SUBMITTED 的 Forbidden 文案已补实义（"status submitted; awaiting approval flip"）。
5. 前端 stale-detail 守卫 + 会话切换绑定已补（`reviewSetDetail` 选中匹配守卫；drawer 以 cursor 会话刷新）。无 XSS（全部 React 转义，无 dangerouslySetInnerHTML）。
6. feature-off 404 在所有权依赖之后解析（先 403/404 所有权，后 404 disabled）——无数据泄漏，接受。

## 与最新 master / open PR 交叉核对（PR 前）

- `git fetch --all --prune`：master 仍 `faa453a8`（无新合并）。
- 执行期新出现分支 `platform/offline-airgapped-profile-v1`、`extensions/gis-pack-sdk-certification-v2`：仅共享 CHANGELOG/UBIQUITOUS_LANGUAGE/config.py 浅冲突面（各自 additive 不同节/键），无方向重叠。
- 本分支零改动：`lifecycle_engine.py`、`mapspec_store.py`（#1356 热区）、`workbenchSlice.ts`（#1353）、spatial_events/migrations（#1355）、geoai 热区（#1336）、claim verify（#1335）。**无需 integration PR**；浅冲突在 rebase 时机械处理（openapi.json/api-docs.md 合后重新生成即可）。

## 回归证据（修复后）

- `pytest tests/review/ --no-cov` → **78 passed**
- `pytest -m cartography --no-cov` → **1125 passed, 3 skipped**（重构等价性 + 既有门禁全量）
- 前端 `vitest run lib/review components/workbench/review-drawer.test.tsx` → **10 passed**；`tsc --noEmit` 0 error
