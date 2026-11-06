# INTERFACE_CONTRACTS — 接口契约（冻结面）

## 后端服务 `app/services/feature_edit/`

### journal.py（纯函数核心，commit 与 replay 同路径）
```python
class FeatureEditJournal:        # 持久形态（map_state JSON）
    entries: list[JournalEntry]  # ≤128 FIFO
    head_seq: int
    dropped: int
class JournalEntry:
    seq: int; kind: str          # apply|undo|redo|materialize
    idempotency_key: str         # 去重主键（apply/undo 均查）
    actor: str; origin: str      # user|agent（服务端定，不信请求体）
    ref_id: str
    deltas: list[FeatureDelta]   # ≤50
    op_summary: dict             # 有界（op 名+参数摘要，无原始几何全量）
    fingerprint_before: str; fingerprint_after: str
    content_revision_before: int; content_revision_after: int
    created_at: str              # 仅展示，不参与排序/校验
class FeatureDelta:
    wfid: str; fields: dict|None # {field: (before, after)} 有界
    geometry_before: str|None    # canonical-JSON 摘要（sha256[:16]）或内嵌小几何
    geometry_after: str|None
# 纯函数：apply_deltas(fc, deltas) -> fc'; invert_entry(entry) -> entry'
# 链校验：entry.fingerprint_before == prev.fingerprint_after
```

### identity.py
```python
def materialize_wfids(features, dataset_fingerprint) -> (features', count)
    # 首次编辑物化：_wfid = "wf1-"+sha256(f"{fp}:{i}")[:16] 写入 properties（不覆盖既有 id 键）
def resolve_target_ids(features, requested_ids) -> {id->wfid}   # FEATURE_ID_KEYS 链
def index_by_wfid(features) -> dict
```

### plan.py
```python
class EditPlan:                  # 持久形态（map_state `_feature_edit_plans`，TTL 600s，≤8）
    plan_id: str; session_id: str; ref_id: str
    expected_content_revision: int; expected_fingerprint: str
    ops: list[EditOp]            # D5 词表
    target_wfids: list[str]      # ≤200 preview / ≤50 safe commit
    safe: bool; validation: list[Finding]   # code 封闭集
    preview: list[FeatureDelta]  # before/after 摘要
    created_at: str; status: pending|applied|rejected|expired|conflicted
class EditOp:                    # discriminated union
    {op: "set_fields", fields: {name: value}}
    {op: "rename_field", from: str, to: str}
    {op: "repair_geometry", fixes: ["make_valid","close_ring","dedupe_vertices"]}
    {op: "move_by", dx_m: float, dy_m: float}                  # 米，必填
    {op: "snap_to_ref", ref_id: str, tolerance_m: float}      # 米，必填（ref_id 搭 registry skip 名单）
```

### executor.py（唯一写路径）
```python
async def commit_plan(...)  -> CommitReceipt      # 锁→CAS→patch→overwrite→journal→失效→戳→广播
async def apply_undo(ref_id, actor, origin) -> CommitReceipt | ConflictError
async def apply_redo(ref_id, ...) -> ...
# CommitReceipt: {entry_seq, content_revision_after, fingerprint_after, changed_wfids,
#                 mapspec_revision|None, stamp_status: stamped|pending|skipped}
# 冲突: ConflictError{code:"stale_revision", current_revision, journal_head_seq}  → HTTP 409 + head diff
```

### 校验 Finding codes（封闭集）
`enum_violation | type_mismatch | unknown_field | feature_not_found | fingerprint_mismatch |
dataset_not_editable | dataset_too_large_to_edit | dataset_read_only | no_snap_candidate |
unit_required | crs_assumption_required | geometry_invalid_after | op_limit_exceeded |
target_limit_exceeded | invalid_field_name | reserved_field`

## REST `app/api/routes/feature_edits.py`（全部挂 require_owned_session）

| Method | Path | 语义 | 错误面 |
|---|---|---|---|
| POST | `/chat/sessions/{sid}/feature-edits/preview` | 目标（selection\|explicit）+ops → plan（纯读） | 400 finding / 404 dataset_not_found（不泄跨租户） |
| GET | `/chat/sessions/{sid}/feature-edits/plans/{plan_id}` | plan 详情（卡轮询/恢复） | 404 |
| POST | `/chat/sessions/{sid}/feature-edits/plans/{plan_id}/decision` | {action: approve\|reject} owner 执行 | 409 stale/conflict、410 expired |
| POST | `/chat/sessions/{sid}/feature-edits/{ref_id}/undo` `/redo` | owner 反向 CAS | 409 conflict + head diff |
| GET | `/chat/sessions/{sid}/feature-edits/{ref_id}/history` | journal 有界列出 | — |
| GET/PUT | `/chat/sessions/{sid}/feature-edits/policy` | mode: confirm_all\|allow_safe_attribute | 400 |

幂等：decision/undo 支持 `Idempotency-Key` 头（复用 IdempotencyMiddleware 语义）+ plan/journal 层 idempotency_key 双保险。

## 工具 `app/tools/feature_edit_tools.py`

| 工具 | tier | side_effect | data_mutations | 说明 |
|---|---|---|---|---|
| `webgis_feature_edit_preview` | 2 | deterministic_compute | — | 目标+ops→plan_id+有界摘要（字段 diff、findings、指纹） |
| `webgis_feature_edit_commit` | 2 | state_mutation | session_state | plan_id→receipt；非 safe 或 policy 不允许 → typed `confirmation_required` |
| `webgis_feature_edit_history` | 2 | cacheable_read | — | journal 摘要（≤20 条） |

tier ≤2（Pi 面可见，非 destructive）。`session_id` 服务端注入。

## MapSpec 窄 intent
`RefreshDataRevisionIntent{ref_id, content_revision}`：仅对 ref 匹配的 source 更新 `content_revision` 键；taxonomy: `("system_repair", "data_revision_stamp")`；origin 仅 system（服务端内部，无 REST codec）。

## 前端 `frontend/lib/feature-edit/`
- `types.ts`：EditPlan/Receipt/Policy 镜像（与 openapi 生成物对齐或手写并契约测试锁定）
- `api.ts`：preview/decision/undo/redo/history/policy client（owner 双头鉴权同 map-action-acks 模式）
- `diff.ts`：FeatureDelta → 展示模型（仿 workbench/delta 纪律）
- `edit-preview-store.ts`：pending plan 预览态（模块 store，会话 epoch 冲刷）
- 组件：`EditProposalCard`（chat）、`EditPreviewOverlay`（地图自管 source `wb-edit-preview`）
- `undo.ts` 扩展：`featureEditCommand()` 构造器（undo/redo 调 REST，CAS 冲突→409 收敛 toast）

## Situation 投影
`compile` 输出增 `feature_edits`（≤5 条最近 receipt：ref、kind、n_features、fields/geometry 摘要、revision、actor）。Interaction IR 13-kind 词表**不动**。
