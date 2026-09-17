# PR body — collab/spatial-review-approval-v1

> **Local evidence only; no online CI wait; do not auto-merge.**

## Summary

在既有 Server-Side Multi-User Collaboration（CollabEventBus/WS, ADR-0119）与 MapSpec Mutation Transaction（ADR-0183）之上，实现**空间审查/会签/批准工作流（Map Review）**：用户或 Agent 提出地图修改 proposal → 审查者按 layer/component/feature/claim 锚定评论 → 按 fail-closed 审批策略会签 → 以 checkpoint + 链式 CAS 回放合并进 MapSpec，全程结构化审计存证与导出。

**Baseline**: `origin/master` = `faa453a8935101378c23eb6694a42c3616d9c670`（执行中 master 未推进；PR 前已复查 fetch --all --prune）

## Phase 0 勘察（.agent-work/collab-review-v1/）

- `00-baseline.md`：open PR 矩阵（#1335/#1336/#1351–#1357 + 执行中新出现的 platform/offline-airgapped-profile-v1、extensions/gis-pack-sdk-certification-v2）——无同方向 PR；evidence/claim graph（#1328）已在 master。
- `01-current-architecture.md`：mutation 门面 CAS/superseded/幂等/守卫语义、checkpoint manifest、bus 封闭词表、User.role、测试约定。
- `02-gap-decisions-ownership-tests.md`：差距矩阵、8 项 DECISIONS、ownership（禁入 #1335 verify、#1336 热区、#1353 workbenchSlice、#1355 spatial_events、#1356 engine/lifecycle）、TEST_MATRIX。

## 主要改动（40 files, +5962/−127）

| 面 | 内容 |
| --- | --- |
| 契约 | `app/schemas/review_schema.py`：ReviewProposal / AnchoredComment / ReviewDecision / MergeEvidence；mutation intents 复用 14 Body union（additive，无第二套 mutation 语义） |
| 策略 | `app/services/review/policy.py`：fail-closed——agent 决策一律不受理；agent 作者需 distinct human 审批；高风险（remove_layer/remove_component/rebind_component/patch_workbench_state）需 distinct reviewer role≥editor；匿名高风险 403；base 漂移旧批准过期 |
| 存储 | `app/services/review/store.py`：per-session 原子 JSON（checkpoint-manifest 同款），损坏 fail-closed，容量有界 |
| 锚点 | `app/services/review/anchors.py`：诚实三态 ok/stale/unverified（未知≠stale；claim 经 #1328 ClaimStore seam） |
| 合并 | `app/services/review/merge.py`：显式 checkpoint → `apply_gis_mutation`(origin=system) 链式 CAS 回放（幂等键 `merge:<pid>:<base>:<i>`）→ intent 失败回滚；**并发交错不回滚**（保护并发方工作，interleaved 存证）；base 漂移 = 冲突拒绝，绝不静默覆盖 |
| 编排 | `app/services/review/service.py`：状态机（TRANSITIONS 闭包）+ 写时策略门槛 + bus 'review' 事件 |
| 导出 | `app/services/review/export.py`：allowlist 投影（无凭据/无 CoT 字段） |
| 单一映射源 | `app/services/mapspec/intent_codec.py`：Body→Intent 从用户路由抽出共用（行为等价重构，400 文案逐字保留，回归全绿） |
| API | `app/api/routes/review_proposals.py`：11 端点 `/api/v1/chat/sessions/{sid}/review/*`；`REVIEW_WORKFLOW_ENABLED=0` → 404（feature-off） |
| 通知 | `app/services/collab/bus.py` additive `review` kind（≤2KB）；前端 protocol/adopt 同步 |
| 前端 | `frontend/lib/review/{store,api}.ts` + `frontend/components/workbench/review-drawer.tsx`：列表/详情/锚态/冲突徽标/策略 verdict/批准/请求修改/拒绝/合并/rebase/撤回 |
| 文档 | `docs/adr/0201-*`、CHANGELOG、UBIQUITOUS_LANGUAGE（Map Review 词表 + 三种 review 歧义标注）、CONTEXT.md |

## Before / After

- Before：多人可同时改一张图（事件同步 + 单笔 CAS），但**谁改的、谁批的、改坏谁负责**无治理面；删除图层等破坏性操作与改视图同一权限。
- After：任意修改可起草为 proposal（含 base revision 锚），审查者锚定评论/会签，高风险操作要求 distinct editor 审批，Agent 提案强制人类审批；合并走既有事务面板（守卫/CAS/幂等/provenance/协作事件全保留），全部决策/合并可导出审计。

## 兼容性 / kill-switch / rollback

- 全部 additive：不注册路由/关 `REVIEW_WORKFLOW_ENABLED` 时与 master 行为一致；旧会话（无 review 目录）→ 空列表；bus 新 kind 对旧消费者透明（前端白名单已同步；WS 透传）。
- 数据 rollback：proposal 存储是会话目录下独立文件，删目录即回退；无 DB 迁移。
- `mapspec_mutations.py` 重构等价性：intent 链逐字搬运至 intent_codec，400 消息不变；`tests/cartography/*` + `tests/test_collab_v6.py` 回归绿。

## 本地验证（Windows / anaconda py3.13 / 无 Redis / in-memory 会话态）

| 套件 | 结果 |
| --- | --- |
| `pytest tests/review/ --no-cov` | **69 passed** |
| 回归：`tests/cartography/test_mapspec_user_presentation_api.py` + `test_mapspec_remaining_chrome.py` + `tests/test_collab_v6.py` | **40 passed**（合计 109） |
| 漂移门禁：`tests/test_api_docs_drift.py` + `tests/quality/test_api_compatibility.py` | **15 passed**（api-docs.md 与 openapi.json 均已刷新） |
| `ruff check <touched files>` | All checks passed |
| 前端：`vitest run lib/review components/workbench/review-drawer.test.tsx` | **10 passed** |
| 前端回归：`vitest run lib/collab lib/workbench components/workbench` | **81 passed** |
| `tsc --noEmit` / `eslint <touched>` | 0 error / clean |
| `git diff --check origin/master...HEAD` | clean |

关键 Oracle 验证连续跑两遍结果一致（见 .goal-loop-ledger.md 第 5/6 轮）。

## 性能/资源

- ReviewStore 每会话 ≤100 proposals、评论/决策有界（500/200）、intents ≤50；文件原子写 O(状态大小)，低频治理对象，无热路径写放大。
- 合并是低频操作，逐 intent 走引擎既有锁/校验；失败回滚复用 checkpoint 机制。
- 测试全量在内存会话态跑（conftest 基线），无网络/LLM/重数据依赖。

## 独立 Review 处置

`review/COLLABORATIVE_SPATIAL_REVIEW_APPROVAL_REVIEW.md`（本分支内）记录独立对抗性 review 的 findings、误报、P0/P1 red-test→fix、剩余 P2/P3 与交叉文件核对结论。

## 已知边界（ADR-0201 §3）

- 合并序列非单事务：并发交错有存证与保护，但中途失败可能留下"部分 intent 已落地"的中间态（rebase 重审收口）。
- 高风险不含 claim 感知加严（v1 同 high 路径）；artifact 锚活性未接线注册表（= unverified）。
- 匿名单人低风险自批是可用性让步（有审计存证）。

## 与并行 PR 的关系

- 不碰：#1335（claim verify 修复面）、#1336 热区（modelops/geoai、embedding-cache、components/geoai）、#1353（workbenchSlice.ts）、#1355（spatial_events/migrations 0092——本分支**无 DB 迁移**）、#1356（lifecycle_engine.py/mapspec_store.py 本分支零改动——intent_codec 是新文件）。
- 浅冲突面（rebase 时机械处理）：CHANGELOG.md、UBIQUITOUS_LANGUAGE.md、CONTEXT.md、`app/core/config.py`（各自 additive 不同节/键）、`docs/api-docs.md`、`tests/quality/snapshots/openapi.json`（合后需重新生成）。

---
`Local evidence only; no online CI wait; do not auto-merge.`
