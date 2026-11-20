# Pi Native GIS Agent Runtime Contract（G01-PI-NATIVE-RUNTIME）

> 状态：与 `zcode/goal-pi-native-gis-runtime-20261107` 分支代码同步（ADR-0286）；
> G12（ADR-0288）与 G22 增补。当前 vendor pin **v1.5.2**（gitlink `b4afdddf`，
> 见 PI_UPSTREAM_COMPAT_MATRIX §4 正式裁决；协议 floor v1.5.1）。
> 本文是 Pi 运行时的**行为契约**——每条声明都有测试锚点；改动契约须改本文 + 测试。

## 1. 角色与唯一事实源

| 事实 | 权威 | Pi 侧消费 |
|---|---|---|
| Agent 运行宿主 | vendored Pi v1.5.2（gitlink `b4afdddf`；G22 前为 v1.5.1/v1.5.0） | `PiBridge` spawn `rpc-entry`（`--no-builtin-tools --no-session`） |
| 工具执行入口 | GIS `ToolRegistry` / `ToolDispatchService` | `dispatch_tool` 唯一管线（capability bind 在 dispatch 内是唯一执行权威） |
| 会话/turn 台账 | `harness_kernel`（turn_journal / TurnStatus） | `begin_turn`/`end_turn`；settle coordinator 是唯一结算编排 |
| 地图事实 | MapSpec / 状态权威 | settle 效果序（`run_settle_effects`）投影 |
| 工具面投影 | `pi_native_surface`（dump + per-turn 激活面） | dump 只在 spawn 写；激活面每 turn 经 `[WEBGIS_ACTIVE_TOOLS:...]` |

**禁止**：平行工具注册中心、平行 MapSpec、平行会话持久化、第二渲染器/编排器。

## 2. Turn 生命周期（PI-01，ADR-0286 D1）

```
CREATED → STARTING → RUNNING ⇄ TOOL_WAIT
             │          │        │
             └────→ CANCELLING → SETTLING → SETTLED | FAILED（终态）
```

- 状态机：`app/services/chat/pi_turn_state.py`（`TurnPhaseTracker`：转移守卫、
  非法转移拒绝+计数、settlement 门闩恰好一次、有界转移轨迹 ≤32）。
- 两条宿主路径（`prompt` / `stream_prompt`）与旗标同点挂载；终相位映射：
  kernel `completed`/`refused` → `SETTLED`，其余（含未知词表）→ `FAILED`
  （fail-closed；细粒度取消/失败差异在 `TurnEvidence.OutcomeRecorder`）。
- **exactly-once settlement**：`_settle_turn_outcome` 的相位门闩在 bridge 边界
  拦截第二次结算（coordinator 幂等收据仍是兜底权威）。未知 kernel status →
  tracker `fail_task`（不再误结算 completed）。
- 观测：`TurnEvidence.to_summary()["turn_phase"]`（相位/轨迹/非法计数/
  settlement 尝试次数）；`task_start` SSE 携带 `runtime_mode`。
- 测试：`tests/unit/test_pi_turn_state_machine.py`（18 用例）。

## 3. 会话与 turn 归属（PI-02）

- turn_id：`turn-<uuid12>`；HMAC-SHA256 turn token 绑 `(sid,tid,iat)`，
  15min / ±30s；路由忽略 caller 路由字段，仅信签名 + live-turn（不活跃 409）。
- stale write 四层拒绝：tracker 迟到 step 丢弃、plan 证据迟到跳过、kernel
  `tool_late` 台账、surface receipt 指纹对照。
- writer lease：`pi-writer:{session_id}` 分布式锁，丢失即 policy abort；
  释放 owner-checked 幂等。
- **resume（DUP-1）**：POST 带 `Last-Event-ID` 是纯重放（同消息 + 有意义 id
  匹配唯一 buffer），绝不重发 prompt；模糊匹配 fail-safe 拒绝。
  测试：`test_sse_resume.py`、旅程 J7（零 LLM 调用）。
- **fork**：不支持（边界已文档化）——进程内 turn 状态不持久化，fork 语义
  会引入平行会话持久化（违反唯一事实源）；`new_session` 隔离是既有能力。
- **进程重启**：`--no-session` 下进行中 turn 不可续（诚实丢失）；客户端经
  SSE ring 重放恢复已缓冲事件（buffer miss → 明确 error，不伪造）。

## 4. 工具能力投影（PI-03）

- 三层：冻结 native 7（`webgis_map_intent` 等，fail-fast 缺失即 abort spawn）
  → 注册超集 dump（spawn 时写，`registry_generation` 锚定）→ per-turn 激活面
  （计划资格 + 32KB 预算 + SurfaceReceipt 指纹）。
- 长尾：**裸名直派**（ADR-0103，注册面名）或 `webgis_execute` 代理——两者同
  dispatch 管线；wrap-native（把注册面工具嵌进代理）双端拒绝。
- 参数 schema：pi 投影与 registry function schema 同源（`_pi_parameters`）；
  服务端 input gate 以共享 Pydantic args_model 二次校验（unknown-field /
  required / 结构错位；oversized 豁免与 registry #699 旁路对齐）。
- 热更新：registry 代漂移在 turn 边界检测 → 无在飞 turn 时回收 worker
  （stop+start，下一 spawn 重建 dump）——进程不重启即可见；在飞 turn 延迟。
  kill switch：`PI_SURFACE_HOT_RECYCLE=0`。
- 测试：`test_pi_dynamic_surface.py` / `test_pi_surface_refresh.py` /
  `test_pi_surface_budget.py` / 旅程 J6（wrap 拒绝）+ J6b（两段式资格 →
  `nearest_facility` 裸名真实计算）。

## 5. 事件与恢复契约（PI-04）

- RPC→SSE：`map_event_to_sse` 纯函数零状态；`agent_settled` 是唯一终判
  （`agent_end` 非终——willRetry/compaction 续跑）。
- **幂等（SSE 泵送路径，即 stream 事件循环）**：同一 `toolCallId` 的
  `tool_execution_end` turn 内首见放行、重复丢弃（metrics
  `duplicate_tool_end_dropped` + evidence warning）；重复 `agent_settled`
  只结算一次（相位门闩）。非流式 drain 循环不消费该事件类型（无去重必要）；
  无 `toolCallId` 的畸形事件不进去重集（不可区分，按原样处理）。重放/resume
  由路由层 ring buffer 承担，不经事件循环。
- 断线重连：`Last-Event-ID`（turn 内单调 id）ring 重放 + gap marker；
  过期/不存在 buffer → 明确 error 事件（`resumed:false`）。
- 披露不丢失：错误/警告/范围未验证信息跨 summary/details/stream 经
  TurnEvidence warnings + settle 披露管线 + SSE additive 字段传递。
- **纯文字任务**：`task_complete` 不含 `map_product` 键、无 `map_finalization`
  事件（负面测试锁定）。
- 测试：`test_pi_sse_idempotency.py` / `test_pi_event_mapper.py` /
  `test_sse_resume.py`。

## 6. 长耗时工具、预算与取消（PI-05）

- 预算：stall 120s（连续静默）/ 整 turn 300s（每 tick 检查）/ 心跳 8s /
  RPC 300s / 扩展工具 TOOL_TIMEOUT_S+5s（AbortController）。
- 取消链：turn CancellationToken → dispatch `use_token` → checkpoint 协作停止
  → typed `cancelled` 响应；abort 快照只点燃目标 pending ids；进程死亡立即
  cancel token。
- **durable job 撤销**：非 clean 终态（cancelled/aborted/failed/interrupted）
  settle 时撤销该 turn 名下非终态 durable job（`pi_durable_cancel`，桥接
  `DurableJobStore.request_cancel`，never-raise）；clean/refused 不撤销。
- 测试：`test_pi_cancellation_unified.py` / `test_runtime_chaos_pi.py` /
  `test_pi_durable_cancel.py`（含 100ms 级短预算边界）。

## 7. 环境治理与三态披露（PI-06）

- 词表（单一定义点 `pi_compat.classify_runtime_mode`）：
  `native`（真 Pi RPC 承载）/ `fallback`（配置 Pi 但不可用，ChatEngine 承载，
  原因码 `vendor_entry_unbuilt` / `pi_process_down`）/ `unavailable`
  （`pi_disabled` / `bridge_not_initialized`）。
- 出口：`/health.agent_runtime_mode(+reason)`、`task_start.runtime_mode`、
  `pi_doctor`（文本汇总行 + JSON `runtime_mode` 字段）。**fallback 不冒充
  native**（`agent_runtime` 徽章与 mode 一致）。
- `pi_doctor` 6 域（vendor/build/extension/surface/rpc/env）；Docker 无凭据
  启动经 `--offline` + env stub 验真（build 缺失 FAIL 而非静默绿）。
  **breaking（本 PR）**：`--json` 顶层从 list 变为
  `{"runtime_mode": {...}, "domains": [...]}`（仓内无旧格式消费方，grep
  核实；仓外消费者需按新结构解析）。
- 测试：`test_pi_runtime_mode_disclosure.py` / `test_pi_status_fail_closed.py`。

## 8. 安全边界（PI-07）

- 子进程最小权限：env 白名单（无 DATABASE_URL/JWT_SECRET_KEY）、
  `--no-builtin-tools`（无 bash/read/write）。
- 回调：HMAC 共享密钥 + 签名 turn token + live-turn 409。
- 权限：tier≥3 / effective_security_tier≥3 双拒；DESTRUCTIVE 逐出 native 面；
  requires_confirmation 名单入 receipt。
- 注入抵抗：用户文本/注入块内同形 `[WEBGIS_ACTIVE_TOOLS:...]` /
  `[WEBGIS_TURN_CONTEXT:...]` marker 中和（唯一合法携带者是 Python 签发的
  控制面）；伪造 token 经 HMAC 拒绝。
- 测试：`test_pi_tools_route_auth.py` / `test_pi_abi_adversarial.py` /
  `test_pi_extension_hardening.py` / `test_pi_durable_cancel.py`（注入段）。

## 9. 性能契约（PI-08）

- 聚合：`pi_surface_metrics.snapshot()["timings"]`（spawn_ready / ttft 的
  p50/p95，窗口 256）+ `surface_recycles` / `duplicate_tool_end_dropped`。
- 预算：激活面 32KB、per-tool schema 16KB、result envelope 4KB 收缩序、
  details 64KB、stdout 单行 16MB、事件队列 1024 带牺牲品逐出。
- 车道：`tests/benchmarks/test_pi_perf.py`（含 scripted turn 墙钟上界 2s 的
  阻塞 I/O 回归闸）；真实 vendor 绝对值由 pi_doctor rpc 域与生产 digest 观测。

## 10. 本地无凭据验证

```bash
# 单元/契约（无需 node/vendor build）
python -m pytest tests/unit/test_pi_turn_state_machine.py tests/unit/test_pi_sse_idempotency.py \
  tests/unit/test_pi_runtime_mode_disclosure.py tests/unit/test_pi_surface_refresh.py \
  tests/unit/test_pi_durable_cancel.py -q

# 真实 vendor（先构建一次）
bash scripts/build-vendor-pi.sh
python scripts/pi_doctor.py                    # 6 域 + runtime mode
python -m pytest tests/integration/test_pi_vendor_contract.py tests/integration/test_pi_schema_coercion_golden.py -q
PI_JOURNEY_E2E=1 python -m pytest tests/integration/test_pi_stub_llm_journeys.py -q   # PI-09 七段旅程
```

## 11. 已知边界（诚实披露）

- 取消旅程的跨线程 SSE 取消窗口在本 sandbox 不稳定 → 取消语义由
  `test_runtime_chaos_pi.py`（16 项确定性）+ 单元级锁定，全栈旅程不含该段。
- `PI_SURFACE_SCOPE_ENFORCE`（面成员硬校验）**默认 ON**（ADR-0288）——发现
  （tool_search）的 discovery grant 提供了注册面内直调的合法路径；kill-switch
  `PI_SURFACE_SCOPE_ENFORCE=0` 回到 G12 前放行行为。
- fork / 跨 turn sequence 不提供（§3）。

## 12. G12 运行时语义（ADR-0288，v1.5.1 pin）

- **原生发现**：`tool_search` / `codemode` 常驻激活（`PI_NATIVE_DISCOVERY=0`
  回退）。长尾超集以 `exposure:"deferred"` 注册 —— 声明面外但可发现、可
  调用；发现命中 **merge** 进 active 集（前门不丢），下一模型请求生效；
  每轮 `before_agent_start` 重置为该轮投影面（发现不跨 turn 泄漏）。
- **discovery grant**：注册超集内的直调若不在 turn selected 面，经
  `pi_bridge_surface_gate` 铸造 turn 内 grant（≤16/turn、幂等、随 turn 回收）；
  echo 对拍 registry **现编译** spec 指纹 —— 撤权/schema 漂移后旧 echo 失效。
  权限面从不因发现扩大（等价 proxy 通道；dispatch 闸序不变）。
- **嵌套调用**：`tool_execution_*` 事件携带 `parentToolCallId` → SSE 投影
  `parent_tool_call_id`（additive）；子事件 id `<parent>/<n>`；终态仍由
  `agent_settled` 唯一裁决；重复 child end 由 toolCallId 终态去重覆盖。
- **完全停止**：用户级停止 = `clear_queue` → `abort`（排队 steer/follow-up
  不再自行重启）；进程停机 = stdin EOF → `PI_GRACEFUL_STOP_TIMEOUT_S`（默认
  8s，vendor dispose 等 tool settlement/credential writes）→ 单次 SIGTERM →
  `PI_TERM_STOP_TIMEOUT_S`（2s）→ SIGKILL。最坏停机时延较 G12 前增加（换取
  vendor 有序清理），turn 内 abort 语义不变。
- **配置代际**：Pi spawn 只经 `model_config.resolve_llm_config()`（单一解析
  点）取配置并锚定 `spawn_config_generation`（不可逆短指纹）；`/config/llm`
  更新后按代漂移在 turn 边界换代 worker（在飞 turn 延迟），响应披露
  expected_generation / recycled / deferred；生产禁动态改配置不变。
- **compaction/恢复**：扩展只记账（before/failed 钩子），不注入自定义
  summary；权威重建走 typed assembly 的 `GIS_STATE_REBUILD` 块（MapSpec/
  SessionPlan 现取：图层含隐藏态、待完成能力、证据 refs；缺失不虚构）。
