# GIS Harness Runtime v2 — 现状审计与架构图（Phase 0 交付物）

- 基线：`master` @ `e8e51c8`（本地 master，领先 origin 7 commits，audit5 系列已含）
- 审计日期：2026-08-29
- 审计方式：5 个并行只读 audit agent（state/lock、harness/Pi、registry、frontend layer runtime、hot-path/Redis/ACK）+ 人工复核关键 P0/P1 证据

## 1. 现状架构图（数据流 + 状态权威源）

```
User
 ↓
Frontend (Next.js)
  ├─ chat-tab → POST /chat/completions/stream
  ├─ use-sse-stream: step_result(mapspec, mutation_revision) → commitMapSpecDocument
  │    + HUD row mount (row.id=ref id, row._mapspecLayerId=spec layer id)
  ├─ composeLiveMapSpec(committed, hud, pending, removed) → MapSpec
  ├─ MapSpecRuntime.reconcileAsync → worker diffSpecs → renderer ops → MapLibre
  ├─ user mutation: toggle/delete/reorder → POST /sessions/{sid}/mapspec/mutations
  │    (patch_layer_presentation / remove_layer / reorder_layers / patch_component)
  └─ observation loop: /cartographic-observation + /map-action-ack → repair_action
 ↓
Chat route (app/api/routes/chat.py)
  ├─ PiBridge.stream_prompt → Pi subprocess (JSON-RPC stdio, pi_rpc_client.py)
  │    ├─ PiTurnRegistry: webgis:pi:active_turn:{sid} (Redis, 15min TTL)
  │    ├─ ensure_session_plan_slot / bind_turn_prompt (session_plan.py)
  │    └─ events → SSE (task_start/step_result/task_complete/session_plan_*)
 ↓
Pi tool callback: POST /pi-tools/execute (HMAC turn token)
  └─ ToolDispatchService.dispatch (dedup → ToolRegistry.dispatch by NAME)
       ├─ 真实 GIS 计算 (app/tools/*, 39 modules)
       ├─ big GeoJSON → session ref store (session:{sid}:data:{ref})
       ├─ _author_display_result → mapspec_store.layer_upsert
       │    → MapSpecLifecycleEngine.apply_mutation
       │        ├─ session_lock_registry.lock(sid, fail_on_degraded/lost=True) ★唯一 fail-closed 路径
       │        ├─ pre_state=get_map_state → prior_revision 捕获          ⚠ F1 在此
       │        ├─ get_mapspec(state_hint) （含磁盘复活路径）              ⚠ F1 在此
       │        ├─ pre_commit_check=_locked_guard （锁内 user-wins 复检）   ✓ TOCTOU 已闭合(patch 路径)
       │        ├─ review_and_repair_cartography (AUTO_SAFE ≤2 iter)
       │        ├─ validate_mapspec(candidate) + prior_blocking fp-cache
       │        ├─ lock.lost 复检 → checkpoint → save_mapspec(rev+1)      ⚠ layers 为第二事务
       │        └─ runtime layers: update/remove_layer_in_state (独立 WATCH/MULTI)
       └─ SessionPlan.apply_tool_result (锁内, 默认降级标志)              ⚠ F2
 ↓
MapSpec (desired) → 前端 reconcile → MapLibre (renderer)
 ↓
Observed state → /cartographic-observation → evaluate_cartographic_session
 ↓
Cartographic QA (HarnessEvaluator) → repair_action (≤1 AUTO_SAFE/次, ≤2 iter)
 ↓
Repair / Replan → Pi
```

### 状态权威源清单

| 状态 | 权威源 | 存储 | TTL |
|---|---|---|---|
| MapSpec (desired) | `MapSpecStore.save_mapspec` (mapspec/store.py:278) | 磁盘 authoritative + Redis cache (`session:{sid}:state` field `mapspec`) | 磁盘 SESSION_TTL+1h / Redis 4h |
| mutation revision (CAS) | engine commit → `set_map_state_fields` (session_data_redis.py:617) | Redis field `_cartographic_mutation_revision` + 磁盘 sidecar `mapspec.json.rev` | 4h |
| runtime layers | `update/remove_layer_in_state` (session_data_redis.py:875) | Redis field `layers` | 4h |
| GISWorldState | `build_world_state` (gis_world_state/state.py:101) | **派生只读投影** | — |
| provenance ring | `append_provenance` (gis_world_state/provenance.py:62) | Redis field `_gis_provenance` (cap 64)，**锁外盲读-追加-写** | 4h |
| cartographic observation/review/context/repair | cartography_runtime.py + chat.py | Redis fields `_cartographic_*` | 4h |
| checkpoints | mapspec/checkpoint.py | 磁盘 only | 磁盘清扫 |
| SessionPlan | session_plan.py | SessionStore alias `session-plan` | 4h |
| 锁 | session_lock_registry → `webgis:sessionlock:{sid}` SET NX PX 30s, 8s 续期 | Redis / inprocess / degraded 三态 | 30s |
| 前端 committed spec | session-cursor.ts (module state) | 内存 | 会话 |
| 前端 HUD rows | layersSlice (zustand) | 内存 | 会话 |

**不能确定唯一权威源的风险点**：desired visibility 同时存在于 MapSpec `layout.visibility` + `cartographic_intent.presentation_owner/expected_visible` + provenance ring 三处，三者由不同路径在不同锁纪律下写入（见 F2/F5/审计-2）。

## 2. 关键发现（复核确认）

### 后端 state/lock（audit-1）

- **F1 [P1] 磁盘复活路径 revision 回退 + 过期 CAS 通过**：`lifecycle_engine.py:526-533` 先从 `pre_state` 捕获 `prior_mutation_revision`；`:557` `get_mapspec(state_hint)` 在 Redis 过期、磁盘 spec 存活时于 `store.py:267-273` 把 `disk_rev=N` 复活进 Redis——但 commit 仍以 stale prior=0 计算 `revision=1`（`lifecycle_engine.py:1124`），N→1 单调性破坏；重放的 `expected_revision=0` 通过 CAS。
- **F2 [P1] 锁降级仍写共享 Redis 的 6 条路径**：仅 MapSpec engine fail-closed（`lifecycle_engine.py:470-472`）；`chat.py:240/1190/1453/1516`、`cartography_runtime.py:167/348/429`、`session_plan.py:238/399`、`plan_mode.py:719` 默认 `fail_on_degraded=False` → 两 pod 各持进程内锁并发写同一共享键（lost update；最坏：DELETE 与在飞 mutation 竞态）。`LockDegradedError` 无任何 route 捕获 → 裸 500。
- **F3 [P2] `lock.lost` 只在 commit 序列前查一次**；`chat.py`/`cartography_runtime`/`plan_mode` 路径完全不查。
- **F4 [P2] MapSpec+revision 与 runtime layers 是两个 Redis 事务**：步骤 4 `set_map_state_fields` MULTI 与步骤 5 `update_layer_in_state` WATCH/MULTI 之间 crash → spec=世代 N+1 而 layers=世代 N。`_rollback_to_snapshot` 用 `save_mapspec(old)` 不带 revision → spec=N/rev=N+1 错配。
- **F5 [P2] provenance 锁外盲 RMW**：并发 mutation 可丢 user 决策条目（ring 是 legacy guard 输入）。

### Harness / Pi / finalize（audit-2）

- **H1 [P0] finalize_display 逐层 mutation**：`layer_manager.py:160-173` 每层一个完整事务（锁/checkpoint/revision/全量 parse×4/全量序列化×3/磁盘写×3）——N 层 ⇒ O(N) 全量周期，且是 409 风暴根因。每次调用新建 engine（`_prior_blocking_cache` 永不命中）。
- **H2 [P1] finalize 隐藏集经 user 路由洗白为 `presentation_owner="user"`**：前端 `layerCommands.ts:555-563` 以 `durable:true` 走用户 mutation 路由提交 agent 决定的隐藏 → 后端 stamp `presentation_owner=user` → agent 此后无法翻回（user-wins 误伤）+ 溯源失真。
- **H3 [P1] upsert 无 ring 撤退 + 重跑换新 id**：ring guard 只覆盖 `PatchLayerPresentationIntent`（`mutation.py:79`）；legacy 会话 agent 可 `upsert(layout.visibility=visible)` 绕过用户隐藏。重跑分析 mint `result-<tool_call_id>` 新 id，旧 hide 天然失效。
- **H4 [P2] SessionPlan apply 失败吞掉**（一次重试后仅日志）；**H5 [P2] AUTO_SAFE set_layer_visibility 不更新 intent stamp**。

### Registry / manifest（audit-3）

- **R1 [HIGH] 无启动期 cross-registry 校验**：`validate_gis_library` 只有测试调用；`init_tools` 吞掉模块注册失败（工具面静默缩水而 AlgorithmRegistry 仍标 native）。
- **R2 [HIGH] 网络工具 parity 缺口**：`network_service_area` 无算法绑定（孤儿）；`location_allocation`/`optimize_route` 无 capability（不可达）；`od_matrix` taxonomy/artifact/model 引用不存在的链；`network_accessibility` 反查到 `shortest_path` capability；`shortest_path`→`isochrone_network` fallback 残留。
- **R3 [HIGH] plan 无 registry 指纹**：MapProductPlan/SessionPlan/VisualizationPlan 均无版本戳；restore 时用当前 registry 重映射 + 匹配 stale `resolved_tool` 行 → 静默漂移（issue #1084）。
- **R4 [MED] 同一 intent 每会话解析 2-3 次**；`capability_tool_map()` 每次重建；`resolve_tool_for_capability` 是仅测试调用的 deprecated shim。

### Frontend layer runtime（audit-4）

- **FE1 [P1] setStyle 永久抹掉 imperative `custom-*` 覆盖层**：只 z-raise 不重挂（`renderer.ts:1000-1014`）；`add_layer`/heatmap 等命令不进 HUD/MapSpec → reconcile/restore 均无法重挂。测试文档自己承认并手工重挂。
- **FE2 [P1] `layer.label` 不上活地图**：compiler 有 `-label` symbol 层，adapter/runtime 完全忽略；`-label` 方言对 identity resolver 不可见。
- **FE3 [P2] source 无 GC**：`removeOrphanCustomLayers` 零调用者；删除层后无引用 source 永久滞留；`add_heatmap_raster` mint `Date.now()` id 不可寻址。
- **FE4 [P2] dual-ID 8 处私有重推导**：declared resolver `layer-identity.ts` 之外，live-spec/restore/evidence/renderer/panel 各自实现；`runtime-evidence.ts:134-135` 用 `hud.id+'__'` 过滤 → ref-mounted 行期望子层集为空 → 观察证据恒 visible:false（饿死修复环）。
- **FE5 [P2] FloatingChrome 仅指针可达**（无键盘/landmark，issue #1079）；顶槽无堆叠（chart/statistics/annotation 同叠 top-left）；export 忽略持久化 placement。

### Hot path / Redis / ACK（audit-5）

- **P1 [P1] 守卫环两次全量读**：`mutation.py:177/208` 为读 64 条 ring 各做一次全量 HGETALL+parse（`get_state_field("_gis_provenance")` 已存在未用）。
- **P2 [P1] chat 准入全量读 tombstone**：`chat.py:515-517`（ACK 路由已改单字段，此旧站未改）；`cartography_runtime.py:211/361` 同款。
- **P3 [P1] ACK 评估每次 2-3 次全量 rehydrate**；eval cache key 含整个 ACK 窗口 → 基本不命中。
- **P4 [P2] fingerprint HSET 在 MULTI 外**；**P5 [P2] ACK Lua 用 EVAL 非 EVALSHA**；**P6 [P2] memory 后端 get_state_field 退化为全量 deepcopy**。
- 已达标（勿动）：`get_ref_data` 单字段（#1064）、spec+revision 单 MULTI（#1073）、ACK Lua 批（#1081）。

## 3. Open issues 对账（gh #1077-1084 中仍 open 的 5 个）

| issue | 状态 | 与本审计对应 | 处置 |
|---|---|---|---|
| #1077 | open | FE2/样式持久（audit5 已做诚实化：durable:false note） | Phase 5 补持久通道 + panel 守卫别名（已随 audit5 提交） |
| #1078 | open | FE1/FE3/FE4 等 8 项 | 大部分已随 audit5 前端批提交（G-2/4/5/6/7/9/10）；setStyle 重挂（G 组之一）→ Phase 5 |
| #1079 | open | FE5 | Phase 9 |
| #1083 | open | lockfile 双轨 | 独立构建问题，非本架构轮范围（记录为 follow-up） |
| #1084 | open | R3 | Phase 4（manifest fingerprint） |

## 4. v2 开发决议（按 /goal 阶段映射）

1. **Phase 1**：F1（复活后 revision 重捕获）、F2（共享写路径 fail-closed）、F3（关键路径 lost 检查）、F4（layers 并入 commit MULTI + rollback revision 对齐）、LockDegradedError 结构化 503。
2. **Phase 2**：H2（finalize 隐藏集服务端 agent-origin 批量）、H3（upsert 家族守卫）、H5（AUTO_SAFE stamp）。
3. **Phase 3**：CompiledRuntimeManifest（启动编译 + cross-registry 校验 fail-fast + O(1) 反查）+ R2 parity 修复。
4. **Phase 4**：manifest fingerprint 入 plan/evidence，restore 比对 → STALE_PLAN（#1084）。
5. **Phase 5**：Layer Mount Registry（setStyle 重挂）、runtime-evidence 身份修复、source GC、layer.label 通道、样式持久 intent。
6. **Phase 6**：P1/P2 单字段化、P6 memory 单字段、guard ring 单字段。
7. **Phase 7**：GISMutationBatch（H1 的根因修复 + finalize 单事务）。
8. **Phase 8**：P4 fingerprint 入 MULTI、P5 EVALSHA、P3 eval 收敛。
9. **Phase 9**：ComponentLayoutRuntime slot 模型 + 顶槽堆叠 + FloatingChrome 键盘可达。
10. **Phase 10/11**：planner 走 manifest 单次解析；模板 descriptor 补 slot/priority 字段。

测试：adversarial 并发（双 worker 同 session：lock degraded/lost、revision conflict、user hide vs agent finalize）、perf 契约（parse/redis-cmd/mutation 计数，非 wall-clock）。
