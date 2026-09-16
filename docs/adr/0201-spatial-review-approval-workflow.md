# ADR-0201: 空间审查/会签工作流（Map Review / ReviewProposal v1）

- 状态: Accepted
- 日期: 2026-09-17
- 线: collab/spatial-review-approval-v1
- 关联: ADR-0119（Workbench V6 协作，本 ADR 的通知平面基底）、ADR-0183（mutation transactions：CAS/幂等/信封，合并回放的语义来源）、ADR-0138（审查 proposal 复用 14 Body mutation 契约）、#1328 Evidence/Claim Graph（claim 锚点来源）、ADR-0195（合并回放同样过空间反幻觉守护网关）

## 1. 背景

CollabEventBus/WS（ADR-0119）解决了**多人事件同步**，MapSpec mutation transaction（ADR-0183）解决了**单笔突变的 CAS/幂等/审计**；但科研/政务制图还需要**可追责的变更治理**：用户或 Agent 提出地图/分析修改提案 → 审查者按 feature/layer/claim 锚定评论 → 批准或拒绝 → 以事务方式合并。此前仓库不存在任何 proposal/审查/批准工作流（Phase 0 全量勘察确认，含全部 open PR）。

词表注意：本 ADR 的 **review = governance review（评审/会签）**，与 MapSpec "lifecycle review" 和 harness "stored review" 无关（UBIQUITOUS_LANGUAGE §Flagged ambiguities 已有前两者的歧义标注）。

## 2. 决策

### 决策一：proposal 是"待审 mutation 集合"，复用而非重建 mutation 语义

`ReviewProposal.mutation_intents` 直接复用 `app/schemas/mapspec_mutation_schema.py` 的 14 Body discriminated union；合并回放**只**经 `apply_gis_mutation`（origin="system"，actor=`review_merge:<pid>`），checkpoint → 链式 CAS（`expected_revision` 逐 intent 推进，幂等键 `merge:<pid>:<base>:<i>`）→ 失败回滚。不建第二条写路径。Body→Intent 映射抽为 `app/services/mapspec/intent_codec.py` 供用户路由与合并回放共用（单一映射源，杜绝漂移）。

### 决策二：存储与 mapspec 同生命周期 —— per-session 原子 JSON，不引 DB 迁移

proposal 治理的是 session 的 mapspec，而 mapspec 本身就是 Redis+盘（无 DB 表）；proposal 沿用 checkpoint manifest 的同款模式（`BASE_STORAGE_DIR/<sid>/review/proposals.json`，temp+replace 原子写，per-session asyncio.Lock 串行化变迁）。损坏 fail-closed（`ReviewStoreCorrupt`）；容量有界（100 proposals/session）。文件即审计载体（`GET .../review/export` 整包导出）。**避让并行 PR**：#1355 已占用迁移号 0092，DB 路线会制造无谓冲突。

### 决策三：fail-closed 审批策略（纯函数，全矩阵可测）

`app/services/review/policy.py`：
- **Agent 一律不得记录决策**（服务层拒收 `Forbidden`，策略层兜底不计数 —— 防御纵深）；agent 决不冒充审查者。
- **Agent 作者的 proposal 需要 distinct human 审批**（任何风险级）——"Agent 不自批"是硬不变量。
- 高风险（`remove_layer`/`remove_component`/`rebind_component`/`patch_workbench_state`，确定性词表）需要 **distinct reviewer 且 role ≥ editor**；匿名会话的高风险一律 403（认证 editor 才可批）。
- 低风险 user 提案允许自批（单人会话可用性）；base_revision 漂移后旧 approve 自然过期（`base_revision_at_decision != proposal.base_revision` 不计数）；当前 base 上的 reject 优先于 approve。
- 角色词汇复用 `User.role ∈ {viewer, editor, admin}`；不新增全局角色/scope（approver 语义是 per-proposal 数据，不是全局身份）。

### 决策四：合并的并发语义 —— 交错保护优先于原子回滚

apply_gis_mutation 的会话锁是**逐笔**的，合并序列不是单事务。失败时：
- intent 被拒（is_error）且无交错 → rollback 到显式 checkpoint（`mr_<pid>`）；
- `superseded`（合并期间他人已提交）→ **绝不回滚**（会摧毁并发方已落地的工作），`interleaved=true` 存证，proposal 保持 approved、rebase 重审；
- 回滚本身失败 → `failure` 后缀 `+rollback_failed` 大声存证。
回滚后 revision 落在 checkpoint 代，base≠current，重试自然被 CAS 拒绝 → rebase 通道强制收口。合并 origin="system" 不受 user-wins presentation 守卫约束（审批即治理覆盖），但 ADR-0195 空间反幻觉网关对所有 origin 生效，合并同样过闸。

### 决策五：锚点 stale 语义 —— 未知 ≠ stale

layer/component 锚：当前 spec 可证缺失 = stale。feature 锚：父层缺失 = stale；数据内联且 feature id 不在 = stale；数据是 ref（不可内联解析）= **unverified**。claim 锚：ClaimStore 可达且库非空而 id 缺失 = stale；store 不可达/空库 = unverified。artifact 锚：v1 不接线注册表 = unverified（边界显式记录）。诚实性优先于覆盖率：绝不把"查不到"伪装成"已失效"或"仍然有效"。

### 决策六：通知与导出 —— additive 扩展 + allowlist 投影

- 协作总线 `VALID_KINDS` 新增 `"review"`（≤2KB：proposalId/event/status/actor；seq 瞬态语义，接收方 refetch 投影）；前端 `protocol.ts` 白名单同步 + `adopt.ts` 落地为 store 回声。
- 导出（`app/services/review/export.py`）走**字段 allowlist**：新增未知字段默认不进导出面；actor 只导 id/kind/role；契约层 MergeEvidence 无 CoT 字段，导出层双保险。

### 决策七：feature-off 与兼容

`REVIEW_WORKFLOW_ENABLED=0` → review 路由全部 404，其余行为不变；旧会话无 review 目录 → 空列表；不注册路由时全站行为与 master 一致。

## 3. 后果

- 正面：治理闭环（proposal→锚定评论→会签→事务合并→审计导出）落在既有 mutation/协作/证据机制之上；Agent 与人类同一契约、同一守卫；策略矩阵与并发语义全部可本地验证。
- 边界（显式接受）：合并序列非单事务（交错有存证与保护，但可能留下"已批准 proposal 的部分 intent 已落地"的中间态，由 rebase 重审收口）；高风险不含 claim 感知加严（v1 与 high 同路径，记录为后续项）；artifact 锚活性未接线；匿名单人会话低风险自批是可用性让步（有审计存证）。

## 4. 实现

- `app/schemas/review_schema.py`、`app/services/review/{policy,store,anchors,merge,service,export}.py`、`app/api/routes/review_proposals.py`、`app/services/mapspec/intent_codec.py`
- 前端：`frontend/lib/review/{store,api}.ts`、`frontend/components/workbench/review-drawer.tsx`、`frontend/lib/collab/{protocol,adopt}.ts`（additive 'review'）
- 测试：`tests/review/**`（69 后端用例）、`frontend/lib/review/review.test.ts`、`frontend/components/workbench/review-drawer.test.tsx`
