# 03 — Harness 生产链路重建模（从代码构造，非 ADR 自述）

基线：master@2aabdc43。图例：〔d〕durable、〔m〕内存易失、→ 边、⟳ 恢复机制。

## 实际生产链（Pi 路径，默认）

```
User Request (POST /api/chat/stream, Last-Event-ID 空 = 新执行)
  ↓ _guard_body_session（owner 校验；DB 连接即借即还）
  ↓ _ensure_pi_bridge_available（池亲和 worker 选择/懒复活 respawn_if_dead）〔m〕
  ↓ _record_frontend_cartographic_observation（turn 开始即写前端观察：viewport/显隐）
      truth: GISWorldState/map_state（session_data，Redis 或内存）〔d〕
  ↓ _build_cartography_turn_context + environment_context（上下文组装）
      truth: cartography_runtime 的 session harness（_harnesses 注册表）〔m〕+ durable evidence
  ↓ TurnEventBuffer 注册到 TurnResumeRegistry（session_key 唯一，断线重连 DUP-1 重放）
      truth: Redis/内存 buffer〔d-有限〕
  ↓ PiBridge.stream_prompt → PiRpcClient(prompt RPC) → Pi 子进程
      turn lease（_TurnLease，池 worker 单 turn 串行）；abort(session) 撤销
  ↓ Pi（vendor）LLM 推理 → 决定调 webgis_execute(innerTool) 或 native 面
  ↓ HTTP 回调 POST /api/pi-tools/tools（签名 turn token → verifiedTurnId + 不可变 sid）
  ↓ dispatch_tool（agent_pi_bridge.py:428）
      面 gate：resolve_pi_tool_call（native/registered surface）；tier>=3 拒绝
      turn RuntimeContext + TurnEvidence 绑定；JobOrigin 绑定
      cancellation：active-turn 表的 CancellationToken（use_token）⟳
  ↓ ToolDispatchService.dispatch（tool_dispatch_service.py:329）
      去重（executed set + _completed_keys）；wave gate（_SessionWaveGate 每会话并发上限）
      ToolRegistry dispatch contract：tier/确认闸、execution_policy（超时/重试）
      → 工具函数执行（app/tools/*，可能走 data_fabric/geocompute/modelops/lakehouse）
      → map_action mint（action_id）+ display authoring（_author_display_result）
      → 结果 ref 化（slim payload、geojson_ref 落 session_data）〔d〕
  ↓ 结果缓存 dispatch_result_cache[(session,toolCallId)]（SSE 适配器读取后清）〔m-短命〕
  ↓ ToolCallEvent → session harness record_event（评估证据）〔m+d〕
  ↓ TaskTracker step（tasks[-1] 若 running；迟到回调丢弃 #993）
  ↓ SSE：tool_execution_end 携 step_result（turn_id 注入、geojson_ref、background_job_ids）
  ↓ （循环：Pi 继续推理直到 final 文本）
  ↓ 前端 applyCommittedMapSpec / map-commands 消费 map_action
      truth: MapSpec（lifecycle_engine CAS revision）〔d〕+ 前端镜像 reconcile
  ↓ turn 结束：_persist_pi_transcript（completed 门内持久化 assistant+title）〔d〕
      TurnEvidence emit_turn_summary（recovery ledger / reliability 反馈）
  ↓ 用户观察回传：POST cartographic-observation / map-action-ack（ACK 持久化）
  ↓ Context Commit（durable_context / gis_world_state 投影推进）
```

legacy 路径（USE_NEW_AGENT=false）：ChatExecutionEngine + plan_orchestrator（规划链）→
tool_pipeline → 同一 ToolDispatchService。

## 每边关键裁决（15 问抽样回答，详证由 Agent A notes 补充）

1. **source of truth**：工具执行结果 = ToolDispatchResult+session_data refs；
   地图 = MapSpec(lifecycle_engine)；能力/工具目录 = 三注册表+runtime_manifest 投影；
   会话评估证据 = cartography_runtime harness（内存）+ durable_context。
2. **cancellation 传播**：abort(session) → turn token → dispatch use_token →
   OperationCancelled → 结构化取消响应（isError=True #1069）。durable jobs 有
   独立 CancellationToken 体系。⟳ 断言：工具内部若不检查 token 则无法真正中断
   （协作式取消，Agent A 审查覆盖）。
3. **crash 恢复**：Pi 子进程死亡 → respawn_if_dead + 准入回退 legacy；
   turn 中断 → TurnResumeRegistry 重放（DUP-1）；active turn 表清理 _cleanup_turn_state。
4. **timeout**：PI_TURN_TOTAL_TIMEOUT=300s（整回合）、PI_EVENT_STREAM_TIMEOUT=120s
   （连续静默）、RPC 层 PI_RPC_TIMEOUT=300s；工具级 execution_policy 独立。
5. **"工具没报错=成功"假阳性**：存在防线（is_suspicious_result、GIS-07 修复
   success=False on authoring failure、visual_evaluator/critique），完整判定由 Agent A。
6. **多套状态机同述一事实的风险**：MapSpec vs GISWorldState vs 前端 layers store
   vs map_state —— 由 reconcile + CAS revision + user-interaction-wins 守卫串联
   （历史 #1070-1078 已修一批，Agent C 复核现状态）。

## 与 V8 的差距（Phase D 输入）

- 规划/资格/成本投影在 Pi 路径依赖 LLM 自主 + promptSnippet；capability retrieval
  （tool_surface_v3 语义检索/重排）服务 legacy 表面与 Pi 的动态注册面，但
  **model/workflow/template/component 不在同一可检索能力图中**。
- qualification（data_qualification.py）存在但按 capability 粒度，未统一到
  model candidate（sensor/bands/resolution/GPU/VRAM）粒度。
- reliability feedback（recovery_ledger）以工具失败为中心，未覆盖
  model/provider/workflow/backend 维度。
- 详见 04 与 Phase D 设计。
