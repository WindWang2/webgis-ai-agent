# Agent A Notes — Harness 生产链路边列表（truth / durability / cancellation 判定）

供主 agent 整合 03-harness-flow.md。生产主链 = **Pi 路径**（USE_NEW_AGENT 默认 true）；legacy ChatExecutionEngine 为回退路径，语义镜像（见 §L）。

## 0. 拓扑总览

```
POST /chat/stream (SSE)
  → route: 鉴权 + TurnResumeRegistry 判定 resume/new
  → get_pi_bridge(session)          # V5-B pool, md5(session)%N 亲和，默认 N=1
  → PiBridge.stream_prompt          # turn 锁（lease #1108）横跨 send+drain+cleanup
      ├─ bind_turn_prompt           # SessionPlan 投影 + 动态工具面 marker + V6 块
      ├─ rpc.request("prompt")      # PiRpcClient, stdin/stdout JSON-RPC
      ├─ 事件泵：events queue + process_died_event 双等待，heartbeat 8s
      │    tool_execution_end → get_cached_dispatch_result(sid, toolCallId)   # ADR-0022 会合点
      └─ agent_settled → maybe_finalize_map_product(final_gate=True) → done
Pi 扩展（vendor/pi 子模块，半信任 + HMAC turn token）
  → POST /pi-tools/execute          # verify_turn_token → sessionId/verifiedTurnId 不可变
  → agent_pi_bridge.dispatch_tool   # 本审计核心边（§2）
```

## 边列表（每边：source of truth / durable / idempotent / crash-timeout 恢复 / 取消传播）

### E1 turn 生命周期（stream_prompt / prompt）
- Truth: 进程内 `_active_turns[session_id] -> _ActiveTurnEntry`（turn_id/run_id/CancellationToken/bridge）+ Redis `webgis:pi:active_turn:{sid}`（pi_turn_registry，TTL 15min，token-checked DEL）。
- Durable: 否（活动态仅内存 + Redis 辅助；turn 产物在下游各 store）。
- Crash/timeout: 子进程死亡看 `process_died_event`（泵内即断，不等 stall 预算）；stall=PI_EVENT_STREAM_TIMEOUT(120s，注释漂移见 A-5)，总预算=PI_TURN_TOTAL_TIMEOUT(300s)；三条失败路径（cancelled/timed_out/send_failed）finally 内 shield+5s abort RPC + 点燃 turn token（HTTP 回调工具在下一 checkpoint 停）。
- Cancellation: 用户 abort → `PiBridge.abort()`：按 session 路由到 owning worker；F5 跨会话守卫；CONC-F1 快照 token/futures；TOCTOU 翻转检查（abort RPC 在飞时 turn 换人 → 只取消旧 token）。断连（GeneratorExit）→ `_abort_on_disconnect`。**判定：取消真向下传播（subprocess + token + pending futures 三面）**。
- Lock: 每 bridge 一把 `asyncio.Lock` + `_TurnLease` 所有权（INV-P1..P4：owner-checked 幂等 release，外层 finally 兜底）。等待锁期间 8s keepalive SSE。

### E2 工具调度边（dispatch_tool → ToolDispatchService → registry.dispatch）
- Truth: ToolRegistry 单一执行真相（别名折叠 `_resolve_tool_name` 在 `_dispatch_impl`；外层指标未折叠 → A-2）。
- 鉴权链: verify_turn_token（HMAC sid+tid, ±30s/15min）→ native/execute/passthrough 分类（resolve_pi_tool_call；registered_surface = model-visible 非 tier-3）→ 存在性 → tier≥3 硬拒 → service.dispatch。
- Durable: 否（执行即发即弃）；产物 ref/账本见 E3/E5。
- Timeout: 每工具 `asyncio.timeout(meta.timeout or 300s)`；THREAD 工具 to_thread + shield + done-callback 归还信号量（放弃等待计数 `_tool_thread_leaked_count`）。**已知边界**：to_thread worker 不可终止，超时后线程继续跑完（结果丢弃）—— 声明的契约。
- Cancellation: turn token 经 `use_token(dispatch_token)` 绑定；OperationCancelled → 结构化取消响应（isError=True + details.cancelled）+ tracker cancel 步（#1069-A-6）；registry 层 OperationCancelled 上抛不被折叠成 TOOL_ERROR。
- 去重: `_session_executed_sets[session]`（进程内，turn 开始清空）+ service 内 `_completed_keys`（post-success 语义，#554-defect-3 修复：bridge 级共享 service 实例）。
- **"没报错=成功"假阳性防护**: dispatch result `success=False` / `is_error_like_result` → metrics error 分类；`_is_suspicious_result`（空要素形态）阻止计划打勾（execution_engine 两路径 + bridge 路径 capability 完成判定在 session_plan 侧消费 dispatch 成功 + artifact 证据）。

### E3 结果落账边（dispatch_tool 成功分支）
按序执行，全部 best-effort 不阻断工具返回：
1. `cache_dispatch_result(sid, toolCallId)` — ADR-0022 会合缓存（SSE 适配器 tool_execution_end 读后即清；128 上限 + 活跃会话保护 + 新 turn 清空）。**bridge 与 SSE 是同一 turn 内两条 task，Pi 等 HTTP 响应后才发 tool_execution_end，顺序有保证**。
2. `apply_tool_result`（session_plan）：**SessionPlan envelope 是计划唯一真相**（Redis/内存 session store，alias `session-plan`）；per-session distributed lock（fail_on_degraded=True → LockDegradedError 被外层 catch 记日志 = 诚实降级不落账）；锁内 lock.lost 守卫；supersede 归档旧 envelope（history alias）。TimeoutError 重试一次（锁竞争）。
3. `maybe_finalize_map_product`（completion/pipeline）: 廉价门（checked_revision + rows_fingerprint + render_observation_seq 三钥匙）→ validate→repair→revalidate ≤MAX_PASSES → 锁内四重漂移守卫（goal/revision/rows/render_seq）后单键 `gis_chapter["map_product"]` 持久化。pending 不披露不落块（降级回收旧终态）。
4. `maybe_update_workflow_instance` / `maybe_update_runtime_projection`：同款门+锁+守卫模式，单键投影（行状态仍只由 _mark_progress 写 —— 非第二事实源，已验证 derive 链同源 build_plan_graph/_NODE_TO_STAGE）。
5. cartography harness 证据（`_persist_cartographic_harness_context`）+ `evaluate_cartographic_session`：锁降级时跳过本帧（不把成功调用变 500）。
- Durable: 2-4 全部落 session store（Redis 持久 / 内存+磁盘 spill）；crash 后由下一触发点或 turn 收尾 final_gate 重验（幂等门保证不重复 repair）。
- 性能注记: 每个成功工具回调串行经过 1-5（各含锁 + Redis 往返）；有指纹门挡住无变化路径，但**回调延迟直接影响 Pi 下一 token**——已见的工程取舍，非缺陷。

### E4 turn 收尾（agent_settled）
- `maybe_finalize_map_product(final_gate=True)`：已存裁决非 READY 强制重验（未解决会话不得滑过 turn 边界）；READY 幂等跳过。map_finalization SSE（含 repairs 时的 mapspec+revision 快照，前端 INV-2 跨会话守卫消费 session_id）。
- 证据链 Stage.USER_OUTPUT + persist_turn_chain（JSONL，GIS_TRACE 开关）。
- task_complete 的 map_product 披露：门跳过时读已存块（read_stored_map_product），`task_complete` = verdict∈READY* ∧ final_map∈verified*（`_is_task_complete`）。

### E5 恢复 / 续行 / 重规划
- **resume_anchor / resume_verify / durable_context**（gis_harness）：anchor 落 session-plane（DATA_DIR .webgis-agent/<sid>/，flock 跨进程）；`RecoveryLedger`（ADR-0119 D5）：(session,tool,class)→attempts write-through JSON + flock + mtime 缓存；TTL 1h 惰性衰减；`copy_between_sessions` 续接预算（resume 后不重置重试额度）。**dispatch 成功路径 record_success 清零**（tool_dispatch_service:916-924 —— 同步 flock IO 有意留事件循环，ms 级锁持有，chaos kill-9 契约排除卡死；已记录为接受的上界）。
- **continuation.decide_continuation**：单一纯函数裁决（预算→不可恢复类→repair→deepen→reobserve→continue）；消费 recovery_state/ledger/observation/qualification。静态检查未见第二裁决点。
- **repair/replan loop**: completion pipeline 内 validate→repair（prior_repairs 跨轮记忆 merge，≤32）→revalidate；workflow_v4/recompute（stale 闭包）+ runtime_bridge stale 投影。
- **legacy plan-mode resume**: livelock guard 只对非 transient 类拒重跑 —— **受 A-1 影响：超时被误判 internal 导致不可恢复**（A-1 修复后恢复正确语义）。

### E6 会话数据 / 上下文组装
- session_data_manager 工厂（import 期零 Redis IO）；Redis 后端 per-loop 客户端 + threading.Lock 交换（GIS-01 修复形态）；L1 cache TTL 2s；singleflight ref 拉取。
- Memory 后端: store/overwrite/get 全锁内；LRU + 字节预算（50MB/会话）+ evict 前 RefSpillStore 落盘（RELOAD_REF durable 半边，hash 路径防注入，TTL 24h）；descriptor store 时一次算好；revision 单调。
- context assembly（legacy）: ChatContextAssembler（budget/truncate/evicted-ref tombstone）；Pi 侧经 bind_turn_prompt 注入同源块（v6_context_blocks 同一 builder，非第二通道）——已抽查确认。
- SSE resume（event_resume）：进程内 ring buffer（256 合并块/turn，4 buffers/session，32 sessions）；resume 永不新建 turn（无重复执行）；live-hold + resume_gap 诚实标记；匿名会话不 hold。

### E7 静态状态机清单（"多套描述同一事实"排查结论）
- 计划事实: SessionPlan.gis_chapter 唯一；plan_graph / workflow_instance / runtime_bridge / product_graph / action_intent 全部为**纯投影**（derive 链同源），行状态单写者 `_mark_progress`。未发现第二事实源。
- 图层/地图事实: MapSpec（mapspec_store）+ map_state（session_data）双存但职责分明（desired vs perception），经 `_cartographic_mutation_revision` + render_observation_seq 对账。
- 执行事实: tracker（UI 步骤）与 registry metrics（执行指标）并行，#993/#1069 已对齐迟到回调归属。
- Legacy plan（PlanStore/CANONICAL_PLAN_EVENT_NAMES）与 Pi SessionPlan 是**两条引擎各自计划面**，events_to_sse 显式禁止跨用事件名（session_plan.py:305-315）——有意隔离，非遗漏。

## L. Legacy 引擎（ChatExecutionEngine）镜像判定
- turn 锁: `session_lock(sid)`（distributed_lock registry，fallback in-process loop-aware）+ `_AcquiredLock` keepalive 轮询（A-7 死赋值，行为正确）。
- 并行工具波: asyncio.wait(FIRST_COMPLETED) + cancel_watch 抢占（F28）；取消时已完成工具先落库（F9 parity #817，persisted 标记幂等）再 cancel 未完成（有界 _cancel_and_await=5s，straggler 不拖锁）。
- 孤儿 tool_call 修复: `_repair_orphaned_tool_calls`（内存+DB 同步补「已取消」占位，幂等）。
- no-progress 熔断 / 空补全拒绝 / max_rounds / 900s 总预算：流式与非流式两路径共用谓词（_is_empty_completion 等）。
- 清会话: `_clearing_sessions`（类级共享，跨实例）+ Redis marker（跨副本）+ 有界 quiesce + 锁空闲才丢弃。

## S. 静态扫描结论（低风险面）
- app/evaluation/**：eval corpora/runner/replay（replay 的 confirm_tier3 永不授予 —— 红线正确）；无生产运行时依赖（只读消费 registry 指纹）。
- app/services/collab/**：leases Lua 原子 + 本地降级有界；bus/presence/delta 为 WS 协作面，未入本次深读（medium/low，结构检查通过）。
- app/services/jobs/**：durable job 运行时（取消探针 500ms + 心跳 + acks_late 终态跳过 + 原子产物）；cancellation/artifacts 为 lib re-export 兼容壳。
- extensions/**：仅 Python 示例 pack（extdemo-*，manifest+health+main），无生产注册路径（extensions_platform 另属 Agent B/D 面）。
- agent/**：skills/文档，无生产代码。
- app/tools/chinese_maps/**：aiohttp 全异步 + provider fallback（TRANSIENT 错误族显式枚举）+ 健康跟踪；未发现同步阻塞。
- app/tools 其余（spatial/remote_sensing/terrain/network/...）：THREAD/ASYNC 策略声明与函数形态一致（policy_audit error 级零发现由测试钉住）；grep 无 time.sleep/requests 阻塞调用。

## 复核过的已修复项（勿重复报告）
CORE-01/03/04/05/06/08/09、GIS-01/05/07、#554-defect-1/2/3、#790、#816/#817/#818、#855、#982、#993、#1069-A5/A6、#1108、SEC-F1、F5/F24/F27/F28、CONC-F1/F2/F7、#1108 lease、ADR-0104 #6(f) ref spill。
