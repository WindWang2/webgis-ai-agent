# Design — 方向 9：Pi ↔ GIS Harness Boundary Convergence

> 配套 recon：`docs/dev/pi-harness-boundary-recon.md`。ADR：`docs/adr/0204-pi-harness-ownership-boundary.md`。

## Problem Statement

`agent_pi_bridge.py`（3294 行）同时承担四类职责：RPC 编排（thin，已由 ADR-0022 F3 拆出）、Pi 工具回调契约、**GIS 后置披露管线（~840 行内联胶水）**、turn 生命周期。GIS 披露管线无形化导致：(a) ok/error 双分支逐行重复；(b) stream/non-stream parity 只能靠 chat 路由手工补（finalization）或干脆缺失（turn-settle 投影/链持久化/checkpoint、process_died tracker 结算错误）；(c) 「增值披露绝不阻断」纪律靠 10+ 处 try/except 复制维持，新增披露点必须再抄一遍。

## Current Architecture

```
Pi vendor (rpc-mode) ──stdin/stdout JSON-RPC──> PiRpcClient ──events──> PiBridge.prompt/stream_prompt
Pi extension ──HTTP /pi-tools/execute──> dispatch_tool ──> ToolDispatchService.dispatch
                                                │
                                                └──（内联 ~840 行）SessionPlan/kernel/MapSpec completion/
                                                    workflow/runtime state/projection/cartography harness/
                                                    gis_trace/no-progress —— GIS 披露管线，无形化
```

## Ownership / Authority（ADR-0204 核心裁决）

| 关注点 | 权威拥有者 | 本 PR 变化 |
|---|---|---|
| 通用 agent loop / turn 生命周期 | Pi vendor + PiBridge（RPC 编排） | 不变 |
| 调度正确性（dedup/ref/自愈/广播） | ToolDispatchService | 不变 |
| 调度资格（surface/tier/capability/input gate） | dispatch_tool 前置闸（Pi 特有守卫 + capability_bind） | 不变 |
| **GIS 后置披露（证据→计划→完成度→投影→链）** | **新 `app/services/chat/pi_post_dispatch.py`（typed pipeline）** | 从 bridge 内联提为显式模块 |
| **turn 结算披露（settle 投影/链持久化/checkpoint/录制）** | **新 `settle_turn_projections`（同模块，双路径共用）** | 新增，消除路由层补丁必要性 |
| MapSpec desired state | MapSpec store（不新增第二份地图真相） | 不变 |
| feature flag 真相 | **新 `hotpath_convergence/flag_registry.py`** | 新增单一 registry + 一致性测试 |

## Canonical Data Contracts（全部 typed、bounded、serializable）

```python
# pi_post_dispatch.py
@dataclass(frozen=True)
class DispatchDisclosure:          # 一次 dispatch 的披露输入（pipeline 唯一入参形态）
    session_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict
    status: str                    # "ok" | "error" | "repeated"
    duration_ms: int
    llm_payload: str
    geojson_ref: str
    map_actions: tuple             # issued 侧动作（bounded [:8] 由消费方裁）
    raw_result: dict               # 引用透明：只读 CAS/指纹等小字段，绝不入 prompt
    turn_id: str                   # verifiedTurnId 优先（迟到回调守卫输入）
    active_turn_id: str

@dataclass
class DisclosureOutcome:           # pipeline 回答「披露后发生了什么」，不携带 Pi 类型
    response_override: Optional[ResponseOverride]  # stale-generation 诚实短路等
    no_progress_hints: tuple[str, ...]
    hard_stop: bool
    stop_note: str
    plan_sse: str                  # 已缓存的 SessionPlan SSE（bridge 转 SSE 适配器）
    finalization_sse: Optional[dict]  # map_finalization payload（非 pending 时）

# turn 结算（stream agent_settled 与 non-stream agent_settled 等价物共用）
async def settle_turn_projections(session_id, turn_id, *, reason, final_gate=True,
                                  state_trigger) -> Optional[dict]  # map_product payload or None
```

pipeline 模块 **零依赖 `agent_pi_bridge`**（防环）；bridge 保留 `PiToolResponse` 翻译与 ADR-0022 缓存。

## State Transitions（不变式）

- 工具生命周期：`begin_step → dispatch → (ok|error) → apply_tool_evidence → 投影推进 → (finalization?)`——顺序即权威序，pipeline 内固定。
- turn 结算：`agent_settled → settle_turn_projections → kernel end_turn`（两路径同序；kernel settle 必须先于 lease 释放，S1 不变式保持）。
- no-progress：`record → 阈值 → hard_stop（token cancel + abort）`——语义不变，仅移位。

## Integration Seams

1. `_dispatch_tool_bound` 步骤 10–16 收敛为 1 次 `apply_post_dispatch_disclosure(...)` 调用 + bridge 组装 `PiToolResponse`。
2. `stream_prompt` agent_settled 分支与 `prompt` 收尾共用 `settle_turn_projections`；chat 路由 non-stream 手工 finalization 调用**保留**（幂等门兜底，无害），但不再是唯一非流式终验触发点。
3. `flag_registry.py` 被 `hotpath_convergence/flags.py` re-export（不破坏既有 import 面）。

## Failure Semantics

- 披露面（evidence/plan/projection/chain/finalization）失败：**绝不阻断工具结果返回**（现状纪律，pipeline 内统一为 per-stage never-raise + debug 日志）。
- 唯一例外：stale MapSpec generation（`_persist_cartographic_harness_context` 返回 False / 锁降级）→ 诚实短路返回成功结果但**不进入**当前 harness 评估（保持）。
- cartographic 评估失败：warn 不阻断（保持）。
- lock contention：`apply_tool_evidence` 重试一次语义（保持，收敛为单实现）。

## Idempotency / Replay

- `maybe_finalize_map_product` 幂等门（complete+revision 一致 → 跳过）不变；settle 管线对 ok→error 交错、重复 settle 均安全（final_gate 双路径都调用幂等门）。
- 迟到回调守卫（#1407 `_late_for_plan`）**整体保留**在 bridge，pipeline 只接收守卫后的输入。

## Security / Permission

- tier>=3 拒绝、verifiedTurnId 会话绑定、`use_origin`/`use_token` 包裹全部保持原位（bridge 前置闸）。
- pipeline 对 `raw_result` 只读；证据字段白名单（`CARTOGRAPHIC_RESULT_EVIDENCE_KEYS`）不变；大 payload 继续走 ref（`_slim_pi_details_payload` 留在 bridge）。

## Resource / Cost

- 无新进程/线程/长驻任务；pipeline 为纯 async 函数组合。
- `settle_turn_projections` 复用现有幂等门，非流式新增开销 = 毫秒级派生读（与流式同）。

## Observability

- 修复 D5：非流式 drain 补 `rt_ev.mark_first_event()`。
- 修复 D6：非流式超时按 `pi_stall`/`pi_turn_budget` 分类（与流式同 failure taxonomy）。
- 披露面失败统一 `logger.debug("[PiBridge][post-dispatch] stage=... ")` 形态。

## Backward Compatibility

- bridge 全部公开符号 + 测试依赖内部符号（`_dispatch_tool_bound`、`_session_executed_sets`、cartography re-exports 等）保留（别名或原位）。
- `ToolDispatchResult`/`ToolCallEvent` 消费形态不变。
- chat 路由现有 finalization 补丁保留（幂等无害）。

## Migration Plan

单 PR 原子迁移：pipeline 模块 + bridge 改接 + settle 共用 + flag registry，测试同步。无双写期（管线调用点唯一，迁移即切换）。

## Rollback / Feature Flag

- 新披露面（非流式 turn-settle 投影推进）是**行为修复**（parity），随 `GIS_RUNTIME_STATE_MACHINE` 等既有 kill switch 关停；不新增永久 flag。
- flag registry 只读盘点，不改默认值 → 零行为风险。

## Acceptance Matrix

| 验收项 | 验证 |
|---|---|
| bridge 行为不回归 | 既有 bridge/kernel/chaos 测试族全绿 |
| pipeline 单元行为 | 新 `test_pi_post_dispatch_pipeline.py`（ok/error 披露序、锁重试、stale 短路、no-progress hard stop） |
| stream/non-stream parity | 新 `test_pi_stream_nonstream_parity.py`（D1–D6 逐项：process_died tracker、settle 投影两路径、链持久化、failure_class、mark_first_event） |
| flag 一致性 | 新 `test_hotpath_flag_registry.py`（registry ↔ 咨询点双向一致、默认值快照） |
| 边界无第二体系 | review：无新 planner/loop/registry；cache 位置未动 |
