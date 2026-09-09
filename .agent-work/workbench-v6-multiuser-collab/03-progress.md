# 03 — Progress（真实执行记录）

## 已完成

### 后端（W1-W6 + W7）
- `app/services/collab/bus.py`：CollabEventBus —— envelope（v1/kind/sid/seq/ts）、
  mutation 派生事件 seq=mutation_revision、瞬态事件 seq=time_ns()、载荷预算闸
  （doc/delta 64KB，其余 2KB，超限 truncated 降级）、Redis psubscribe listener
  （退避重连）、进程内降级直发（单扇出路径纪律：Redis 模式发布者不本地直发）。
- `app/services/collab/presence.py`：Lua 原子 join（cap 32 + 过期清扫单脚本）、
  TTL 30s、心跳合并（服务端白名单裁剪：label/kind/color/viewport/selectionIds
  ≤50/editingLayerId/editingGroupId）、快照惰性清扫、进程内有界降级。
- `app/services/collab/leases.py`：Lua acquire/renew/release（token 校验）、
  TTL 60s、每 client ≤16、lockKey 白名单（layer:|group:）、release_all 断连清理、
  过期惰性清扫。advisory 语义（agent 硬闸是引擎 lock 守卫，见下）。
- `app/services/collab/delta.py`：delta 纯函数管线（validate + apply），
  绝对值语义、固定应用次序（setGroups→removeGroupIds 级联→membershipSet
  （目标存在性）→membershipClear（Set 优先）→locks）、DeltaError 400 语义。
- `app/api/routes/ws_collab.py`：`/ws/collab/{session_id}` —— 双前端认证
  （bearer JWT + token_version / session owner_token hmac；交叉使用 4003；
  legacy NULL fail-closed）、独立 rate 桶 `ws_collab:{ip}`、accept→订阅→读
  权威状态→hello 时序、ping 捎带新鲜 revision、sync 回放、presence 200ms
  服务端合并节流、lease 三操作、入站白名单 + token bucket(20/s) + 16KB 上限、
  有界外发队列（满则断开慢消费者）、finally 清理 presence/lease/订阅。
- `app/services/mapspec/lifecycle_engine.py`：
  - `MapSpecResult.error_code`（additive，layer_locked / workbench_delta_*）；
  - 内建 agent locked-layer 守卫（engine 层 —— R1-C1：agent 工具直连引擎
    绕过门面；单发 + apply_presentation_batch 双路径；family 语义）；
  - `SetWorkbenchStateIntent.base_workbench_revision`（workbench 级 CAS，
    R1-C2）+ `_rev` 盖章（commit 时 = mutation_revision）；
  - `PatchWorkbenchDeltaIntent`（delta 管线 + 结果级全量校验 + `_rev`）。
- `app/services/gis_world_state/mutation.py`：成功 mutation → bus 事件
  （doc/delta/presentation/op；batch 净效果逐层 presentation + 单条 batch op）；
  superseded/error 零事件（测试锁定）。
- `app/api/routes/mapspec_mutations.py`：`patch_workbench_delta` intent 路由 +
  `base_workbench_revision` 字段 + `GET /chat/sessions/{session_id}/workbench/state`
  （新鲜读；`?meta=1` 定向 revision 探测，不物化全量 map_state）。
- `app/main.py`：路由注册 + lifespan 启停 collab bus listener。

### 后端测试（tests/test_collab_v6.py，27 例全绿）
- delta 纯函数 7（部分字段/创建需名/级联删除/set+remove 冲突/Set 优先/锁/未知域拒绝/重放幂等）
- presence 2（cap 32 + TTL 过期清扫；心跳白名单合并）
- lease 4（生命周期/仅持有人可续可释；TTL 过期释放无孤儿；client 上限 + 非法 key；release_all）
- bus 2（信封/seq 语义/预算 truncated；本地扇出/退订/不串道）
- 引擎 6（delta 增量 + `_rev` 盖章 + 结果级校验拒绝造环；悬空 membership 400；
  base_workbench_revision CAS（无关 mutation 推进 revision 后陈旧 base 被拒）；
  lock 守卫单发+family+upsert 重建拒绝+用户不受限；batch 路径 refused 不静默）
- 门面挂钩 3（doc+op 事件 seq=revision；delta+presentation 事件；superseded 零事件）
- WS 3（认证矩阵 7 场景；ping/sync 环回；扇出末段 send_loop 单扇出无双计）

### 修复的测试暴露 bug
- presence `_record` 硬编码 TTL → 实例 ttl_s（测试锁定）。
- ws_collab finally 哨兵被 closed 门短路 → send_loop 永不自然结束
  （enqueue 对 None 哨兵放行 closed 门）。

### 邻接回归
- `tests/unit/test_mapspec_lifecycle_engine.py`（V5 workbench 33 例中含）
  + `tests/test_ws_auth.py` + `tests/test_ws_service.py`：33 passed。
- `tests/unit/test_gis_world_state.py`（门面）：9 passed（与 collab 合跑 36 passed）。
- ruff（改动文件）：All checks passed。

## 测试基建发现
- `MemorySessionStore` 落盘（USE_REDIS=false 时）→ 会话状态跨 pytest 进程
  残留：固定 sid 会在重跑时命中旧 `_cartographic_mutation_revision`。
  本文件测试以 uuid 前缀 `_sid()` 隔离（repo 既有引擎测试用固定 sid，
  CI 全新环境无感 —— 潜在 flake 已记 follow-up）。

## 待办
- W8 artifact-status 投影端点（后端收尾）
- W9-W16 前端全部
- OpenAPI snapshot 属主刷新 + contract drift（新 intent + 新端点）
- ADR + CHANGELOG
