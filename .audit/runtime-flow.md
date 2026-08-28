# Runtime Flow — 真实调用链（2026-08-27）

## 一次对话（legacy 默认路径）

1. 前端采集 map_state（视口/图层显隐）→ `POST /api/v1/chat/stream`（use-sse-stream.ts）
2. `chat.py` 准入：会话归属守卫 → `_use_pi_bridge()` 裁决路径
3. ChatExecutionEngine：
   - context_assembler 组装（系统提示 + 历史 token 预算截断 + map_state + layer inventory 20 行 + project context）
   - `_maybe_plan` → plan_orchestrator（LLM plan 或 `_synth_plan_from_harness` 确定性合成）
   - ToolCatalog 域关键词裁剪 → LLM 循环（max 60 rounds，no-progress 熔断，900s 预算）
   - ToolExecutionPipeline.execute_tool_call → ToolDispatchService.dispatch：
     ref 解引用 → 工具执行（sync 工具 to_thread + 信号量）→ 结果 slim（MSG_MAX_CHARS=2500）
     → 大结果挂 ref: → MapSpec authoring（fingerprint 证据）→ error 契约/correction_hint
4. SSE 按序下发：token / tool_call / tool_result / mapspec / mutation_revision / 心跳
5. 前端 use-sse-stream 分发：token 渲染、图层 fetch（>5000 走 MVT）、mapspec → session-cursor.commit
6. MapSpecRuntime diff → MapLibre 增量应用
7. 观测：reconcile settle → runtime-evidence 采集（含 getStyle 读回）→ POST /observation（fingerprint+generation 双门）
8. cartographic gate：PiAgentHarness 证据 + evaluate_cartographic_session（收敛判定）→ finalize_display 收口
9. finalize：PatchLayerPresentationIntent 持久化 + 前端 visibility 事务（identity→desired→runtime→durability→postcondition）
   → ack（confirmed/visible/hidden/unresolved）→ gate 计分

## 一次对话（Pi 路径，opt-in）

同上 1-2；PiBridge 全局锁 → JSON-RPC 子进程 → vendor loop 规划/执行 webgis_execute 回调
→ /pi-tools/execute（HMAC turn token + is_active_pi_turn）→ dispatch_tool → 同一 ToolDispatchService
→ pi_event_mapper → SSE；转录落库在路由层 `_persist_pi_transcript`；TaskTracker 借 get_engine().tracker。

## 用户 mutation（desired state 写路径）

```
UI 事件（toggle/opacity/remove/drag）
  → 乐观写 useHudStore / transient state（拖拽 rAF，pointerup 单次 commit）
  → visibility-transaction.ts（agent 链已串行）或 user-mutation.ts（用户链，无串行 — ST-P1-2）
  → POST /api/v1/chat/sessions/{sid}/mapspec/mutations（origin=user, expected_revision 必填）
  → MapSpecLifecycleEngine.apply_mutation（分布式锁；CAS 校验；COW；save→盘→Redis 顺序）
  → 200（新 revision+spec）→ applyCommittedMapSpec 回灌 ｜ 409 superseded → 回灌服务端真相
```

## 恢复路径

- Reload: use-workspace-session → 重置全部 store + cursor（防跨会话污染）→ GET /map-state
  → commitMapSpecDocument + syncSpecLayersToStore + presentationFromMapSpec 终覆盖
- SSE reconnect: 有界重连 2 次 → Last-Event-ID → 后端只读 replay → id 去重 + revision/spec 收敛
- Checkpoint rollback: 全量 blob 预检 + TOCTOU guard；显式命名快照 self-contained

## 制图 QA 环

```
MapSpec 提交 → evaluate_cartography_semantics（40 规则：CRS/BBOX/几何/字段/分类基数/色带可分性…）
  → quality_loop：AUTO_SAFE 修复（只动呈现色，≤2 轮，重复补丁/失败终止）
  → fingerprint → 前端观测对照（desired vs observed）→ ack 收敛
  → project_memory：shared_classification 指纹记忆 + distribution_drift 失效
```

## 关键裁决点

| 问题 | 现状 |
|---|---|
| source of truth | 后端 MapSpec（磁盘权威 + Redis 缓存） |
| mutation revision 拥有者 | map_state._cartographic_mutation_revision（engine 锁内推进） |
| 绕过 CAS 的操作 | 全部 agent 工具（除 component_update 可选 CAS） |
| user vs agent 冲突 | 用户路径强制 CAS + superseded 回灌；组件经 expected_revision；agent finalize 豁免 _userPinned 层 |
| SSE 丢帧 | Last-Event-ID replay（只读）+ id 去重 |
| MapLibre 与 spec 不一致 | observation 读回 → repair 链（bounded） |
