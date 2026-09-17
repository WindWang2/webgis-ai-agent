# CURRENT_ARCHITECTURE — 与本方向直接相关的既有机制（深读记录）

## 1. MapSpec mutation 事务（合并必须经过的路径）

### 门面 `app/services/gis_world_state/mutation.py`

```
apply_gis_mutation(session_id, intent, *, origin="agent", actor="unknown",
    expected_revision=None, engine=None, mutation_id=None,
    client_optimistic_id=None, turn_id=None, reason=None,
    producer_class=None, explicitness="explicit") -> MapSpecResult
```

- 链路：信封铸造（MutationEnvelope.from_request）→ user-wins 守卫（pre-lock ring +
  锁内 pre_commit_check 复检）→ `engine.apply_mutation`（锁/CAS/COW/事务）→
  provenance ring 追加 → collab 总线事件发布（best-effort）。
- `apply_gis_mutation_batch`：仅接受 `PatchLayerPresentationIntent` 列表，单事务。
- origin ∈ {agent, user, system}；**user 必须带 expected_revision**（否则 error）。

### 引擎 `app/services/mapspec/lifecycle_engine.py::apply_mutation`

- per-session 分布式锁 `session_lock_registry.lock(session_id, fail_on_degraded=True,
  fail_on_lost=True)`；锁降级/丢失 fail-closed（#1071）。
- CAS：`expected_revision != pre_state["_cartographic_mutation_revision"]` →
  `MapSpecResult(superseded=True, mutation_revision=prior, mapspec=loaded)`；
  路由层把 superseded 映射 HTTP 409。
- 幂等：`mutation_id` 锁内去重先于 CAS（重放返回当前 revision，duplicate=True）。
- 空间反幻觉守护网关（SPATIAL_GUARDRAILS=0 可关）锁前校验几何意图。
- CheckpointIntent（可具名）/RollbackIntent：checkpoint 落盘
  `app/services/mapspec/checkpoint.py`（内容哈希 manifest + blobs/，原子写）；
  rollback 整 spec 恢复。
- `MapSpecResult` 关键字段：is_error / superseded / duplicate / committed /
  mutation_revision / mapspec / error_msg / correction_hint / mutation_id /
  producer_class / error_code / locked_layer_ids / locked_component_ids。

### HTTP 适配 `app/api/routes/mapspec_mutations.py`

- `POST /api/v1/chat/sessions/{session_id}/mapspec/mutations`：14 个 intent 的
  discriminated union（`app/schemas/mapspec_mutation_schema.py`，每个 Body 自带
  `expected_revision` + 可选 `client_mutation_id`）；锁竞争/降级 → 503；
  superseded → 409；`is_error` → 400。
- auth：`Depends(require_owned_session)`（`app/core/auth.py`）→ Conversation
  所有权（user_id 匹配，或匿名 owner_token hmac 比对；legacy NULL fail-closed）。
- `GET .../workbench/state`（新鲜读 + meta 轻量 revision 探测）、
  `GET .../workbench/artifact-status`（collab service 缝只读投影）。

## 2. 协作通知平面 `app/services/collab/bus.py`

- 事件信封 `{v:1, kind, sid, seq, ts, data}`；kind 封闭词表
  `{doc, delta, presentation, op, presence, lock, artifact}`（`VALID_KINDS`）。
- mutation 派生事件 `seq = mutation_revision`（replay cursor）；doc/delta ≤64KB、
  其余 ≤2KB，超限降级 truncated。
- Redis PUBLISH `webgis:collab:{sid}` 单扇出；无 Redis 进程内降级。
- WS 路由 `app/api/routes/ws_collab.py`：JWT mode（user）/session mode（匿名 +
  owner_token）双认证；`_make_listener` 无差别转发全部信封 → **新增 kind 服务端
  additive 即达前端**。

## 3. 角色 / 租户

- `User.role ∈ {viewer, editor, admin}`（`app/models/db_model.py` CheckConstraint）。
- 会话级所有权：`authorize_session_write` / `require_owned_session`；
  `require_admin` 存在；`actor_ids()` 归一 (user_id, org_id)；匿名哨兵折叠。
- 组织审计 `app/services/audit.py`：`record_audit(db, action, ...)` fail-open，
  词表前缀 admin./quota./auth.（词表外归 `uncategorized.<action>`）。

## 4. 存储

- 会话状态：`session_data_manager`（Redis hash + 进程缓存；`get_state_field` 定向读）。
- MapSpec 深存储：`app/services/mapspec/store.py`（`BASE_STORAGE_DIR` 下每会话目录，
  原子写 JSON + 磁盘复活）；checkpoint manifest 同目录约定。
- **ReviewStore 决策**：沿用 per-session 目录 + 原子 JSON 写 +
  per-session asyncio.Lock —— 低频治理对象不需要 DB 表/迁移；文件即审计载体（可导出）。

## 5. 测试约定

- pytest：`pytest.ini` asyncio_mode=auto；`--cov=app` 默认开（targeted run 加 `--no-cov`）。
- API 路由测试模式（`tests/cartography/test_mapspec_user_presentation_api.py`）：
  裸 FastAPI() + `include_router(router, prefix="/api/v1")` +
  `dependency_overrides[require_owned_session]` + httpx ASGITransport AsyncClient。
- marker：`cartography`（确定性发布门禁，无 Node/LLM/网络）、`heavy`、`real_services`。

## 6. 前端（待 Subagent A 报告补全）

- `frontend/lib/workbench/*`（doc/delta/persistence/layer-lock/session-anchor/undo）
- `frontend/lib/collab/*`（client/protocol/store/adopt）— protocol 词表需确认是否
  校验 kind 白名单（决定 review 事件是否需要前端同步扩展）。
- `frontend/components/workbench/*` — review drawer 挂载点。
