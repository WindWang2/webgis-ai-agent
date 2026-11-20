# G32 INTERFACES — 对外交互契约

## REST(base `/api/v1`,prefix `/chat`;全部 `require_owned_session`,actor 服务端裁决)

| 端点 | 方法 | 说明 |
|---|---|---|
| `/chat/sessions/{sid}/feature-proposals` | POST | 创建(submit=true 即 submitted);op 级 wfids 服务端归一化为 wfid |
| 同上 | GET | 列表(status 过滤/分页;状态排序由前端) |
| `…/{pid}` | GET | 详情(ops/decisions/comments/resolutions/approval 投影) |
| `…/{pid}` | PATCH | 内容更新(expected_revision CAS;approved 更新自动降级 submitted,旧批准失效) |
| `…/{pid}/diff` | GET | 三方差异(base/current/candidate,分页;几何摘要) |
| `…/{pid}/conflicts` | GET | 冲突报告(verdict/conflicts/satisfied/independent) |
| `…/{pid}/resolutions` | POST | 提交裁决(keep_current/take_proposed/use_new_value;新值经 G14 验证器) |
| `…/{pid}/rebase` | POST | 预览(apply=false)或执行(apply=true;新 base 快照+新 revision) |
| `…/{pid}/decisions` | POST | submit/approve/request_changes/reject(approve 绑定 digest+revision+指纹,强制 clean) |
| `…/{pid}/merge` | POST | approved→merged(提交桥);merged 幂等重放持久回执;recovery_required 拒绝盲重试 |
| `…/{pid}/recovery` | GET | 只读恢复视图(verdict/缺失证据/处理路径) |
| `…/{pid}/recovery/resolve` | POST | 证据门恢复(executed→merged;not_executed→approved;unprovable 拒绝) |
| `…/{pid}/comments` | POST | 评论(指向 revision,过时可辨) |
| `…/{pid}/audit` | GET | 审计导出(allowlist 投影,无内部路径) |
| `…/{pid}/withdraw` | POST | 撤回(共享 base blob 引用计数检查后才物理删除) |

错误码:404 proposal_not_found/dataset_not_found;409 state_conflict/approval_invalid/unresolved_conflicts/proposal_revision_conflict/recovery_required/merge_window_*;410 snapshot_unavailable/snapshot_corrupt;422 invalid_ops/resolution_rejected/dataset_read_only_source;429 draft_limit_reached。

## Agent 工具(tier 2;无批准权 — decision/merge/resolutions 面不存在)

- `vector_change_proposal_create(ref_id, ops, feature_ids?, title?, submit)` — state_mutation
- `vector_change_proposal_list(status?, proposal_id?)` — cacheable_read
- `vector_change_proposal_diff(proposal_id, page?)` — cacheable_read
- `vector_change_proposal_rebase_preview(proposal_id)` — cacheable_read
- 返回紧凑对象(proposal_id/status/revision/digest/计数/next_action);Pi 发现:deferred 面经 `tool_search`(描述含中文关键词保证 BM25 命中)。

## G14 最小适配(向后兼容,已过全部 123 项既有回归)

1. `build_plan(..., plan_seed: Optional[str])` — plan_id 可由调用方 seed(G32 传 fpr- op_seed;空值回退 fep-uuid)。
2. op 级可选 `wfids` — `set_fields/repair_geometry/move_by/snap_to_ref/delete_features/replace_geometry` 按 `解析目标 ∩ op.wfids` 限定;create/split/merge/rename 拒绝该键;geometry ≤10 上限按几何 op 实际触及并集;build_plan 在物化预演形态上归一化自然 id→wfid 后持久。
3. `EditOpModel.wfids: Optional[List[str]]`(1..200)。

## 存储

- 提案真相:`<MAPSPEC_STORAGE_DIR|DATA_DIR/.webgis-agent>/<sid>/feature_proposals/proposals.json`(v1,原子写+fsync,分布式锁 fail-closed,merge 租约 TTL 300s 同写)。
- base 快照:BlobStore CAS(key=canonical sha256;digest 校验;≤8MB);多提案共享零重复;withdraw 后引用计数检查才删。
- 状态机:draft→submitted→(changes_requested↔)→approved→merging→merged;recovery_required(证据门);withdrawn/rejected/merged 终态。

## 前端

- `frontend/lib/api/feature-proposals.ts`(typed client)、`frontend/lib/proposals/store.ts`(useSyncExternalStore)。
- `frontend/components/workbench/feature-proposal-drawer.tsx`(open/onClose;队列/详情/裁决/批准/merge/恢复)。
- 入口:`feature-edit-toolbar.tsx` 提案队列按钮(`data-testid="feature-proposal-open-queue"`)。
- i18n:`featureProposals` 域(zh-CN/en-US)。

## 供后续任务复用

- `feature_proposals.conflicts.classify_conflicts` — 纯函数三方分类(G31 可复用做跨层检查的要素级对齐)。
- `candidate.resolve_ids_on_payload` — 未物化/已物化双形态自然 id↔wfid 解析。
- `bridge.assess_recovery` 三态恢复判据模式。
