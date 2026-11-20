# G43 INTERFACES — 对外契约面

## 数据模型（migration `g43_turn_context_receipts`，ADR-0295）

- `turn_context_receipts`：`turn_id` 唯一；`status ∈ assembled/not_sent/submitted/unknown`（单调），
  `disposition ∈ started/queued/handled`，`turn_outcome ∈ completed/failed/cancelled`（独立维度），
  `not_sent_reason ∈ model_budget_rejected/model_apply_error/pi_send_error/image_gate_rejected/assembly_rejected`，
  `payload` = receipt.as_bounded_dict()（有界，无全文/秘密）。
- `session_source_policies`：`session_id` PK；`mode ∈ auto/explicit`；`preferred/excluded` JSON
  （server-minted `pk:/cm:/gm:block`）；`policy_version` CAS。
- `messages.context_receipt`：JSON 可空（G36 attachments 同款加性列）。

## 源 ID 命名空间（闭集，服务端铸造）

`pk:<ProjectKnowledgeEntry.id>` · `cm:<CartoProjectFact.id>` · `gm:block`（gis_memory 整块，块级粒度）。
结构性非法 → `source_id_malformed`；合法结构但不在授权目录 → `source_not_authorized`（统一 400，不泄漏存在性）。

## REST（`/api/v1/chat/sessions/{sid}/...`，owner 守卫 + 项目级 get_project_with_auth）

| Method | Path | 说明 |
|---|---|---|
| GET | `context-sources?project_id=` | 授权可选资料目录（含权威 version_token） |
| GET/PUT | `context-policy` | 读/CAS 写（409 = POLICY_VERSION_CONFLICT + current） |
| POST | `context-preview` | 无副作用预览（is_preview=true；not_previewed 域如实列出） |
| GET | `context-receipts?limit=` | 最近回执列表 |
| GET | `context-receipts/{turn_id}` | 单 turn 完整回执（404 = RECEIPT_NOT_FOUND） |

## SSE（additive，task_start 之后）

`turn_context_receipt`：{session_id, turn_id, receipt_id, digest, status, disposition, turn_outcome,
policy_version, mode, counts{included,omitted,truncated,sources_total}, persisted}。

## @tool

`webgis_turn_context_receipt(session_id 注入, turn_id?, limit?)` —— 只读（side_effect=pure；
签名仅 session_id/turn_id/limit）。经 `tool_search` 发现后直调（tier 2 动态面）。

## 内部接缝（薄适配）

- `bind_turn_prompt_with_receipt` → `(message, receipt|None)`；`bind_turn_prompt` 保留原签名。
- `SharedTurnFacts.source_policy`（fetch_shared_facts 与 legacy_compat 两编排点加载，冻结于本 turn）。
- `ProviderReport.source_refs`（渲染时刻捕获的源去向；BaseProvider tuple 返回向后兼容）。
- `ContextAssemblyReceipt.source_lines/policy_version`（schema `ctx.receipt.v2`；digest 覆盖源行）。
- 显式策略 + caller 预拼 cartography block → `ContextAssemblyRejected`（typed 拒绝，不回落）。

## 前端

- `lib/api/turnContext.ts`（client + PolicyConflictError）；SSE 事件 reducer
  `attachTurnContextReceipt`（turn 幂等 + 状态单调）。
- `components/chat/turn-context-panel.tsx`（消息内展开回执；按需拉取完整回执）。
- `components/chat/turn-context-sources.tsx`（资料工作台：优先/排除/预览/CAS 冲突）。
- 恢复面：`GET /chat/sessions/{id}` 的 `messages[].context_receipt` → `turnContextReceipt`。
