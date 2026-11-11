# INTERFACES — G12-PI-NATIVE-INTEGRATION

## 本任务独占文件（其他任务勿改；冲突时以本文件为准）

- `vendor/pi`（gitlink）、`scripts/build-vendor-pi.sh`
- `app/extensions/webgis-tools/index.mjs`
- `app/services/chat/pi_rpc_client.py`、`pi_event_mapper.py`、`pi_native_surface.py`、
  `pi_tool_spec.py`、`pi_surface_receipt.py`、`pi_compat.py`、`pi_availability_adapter.py`
- `docs/PI_UPSTREAM_COMPAT_MATRIX.md`、`docs/PI_RUNTIME_CONTRACT.md`、pi_doctor
- Pi 专属测试：`tests/integration/test_pi_*`、`tests/test_pi_*`

## 消费的既有契约（只读/最小 additive）

| 契约 | 用法 | 变更 |
|---|---|---|
| `CapabilityAvailabilitySnapshot` / `compile_availability_snapshot()` / `project_pi_tool_surface()` | W03 资格投影的事实来源（snapshot supplier 可注入） | 不改 Python 资格政策 |
| `app/tools/descriptor.py`（tier/side_effect/model_visible） | exposure/annotations 派生 | 只读 |
| ToolDispatchService / dispatch_tool | 所有 direct/deferred/codemode/嵌套调用同一执行权威 | 只加 discovery grant 判定，不动闸序 |
| MapSpec/LifecycleEngine/agent_undo（#2035） | 写工具结果按现有 CAS/幂等 | 不动 |
| typed context assembly（context_assembly/*） | W09 GIS 状态重建块 = 新 provider，走既有 scope/budget/receipt | 新增 provider 文件，不改核心 |
| SessionPlan / ArtifactRegistry | 恢复事实源 | 只读 |

## 对 G13–G16 的暴露（additive，旧消费者可忽略）

1. dump v4：`{version:4, tools:[{...v3, exposure, namespace?, annotations?}], default_active,
   execute_proxy, abi?, registry_generation?, surface_fingerprint?}` — v3 解析方按 v3 读不受影响。
2. SSE step payload 新增可选 `parent_tool_call_id`（嵌套子步骤）；无该字段 = 顶层（旧行为）。
3. `pi_surface_receipt`：新函数 `record_discovered_tool()`；receipt 新增可选 `discovered` 列表
   （additive；不改 `selected` 语义）。
4. `PI_NATIVE_DISCOVERY`（默认 on；=0 回退今天行为）、`PI_SURFACE_SCOPE_ENFORCE`（G12 后默认 on，
   =0 回退）、`PI_GRACEFUL_STOP_TIMEOUT_S`（默认 8）三个 env 开关，全部写入 compat 文档。
5. 模型配置：`model_config.resolve_llm_config()` 成为 Pi spawn 唯一来源；新 `pi_config_generation()`
   只读函数（诊断面用）。/config/llm 行为变化 = Pi-only 下真实换代 + 期望/生效代披露。

## 共享文件触碰清单（对 #2032/#2021/#2022 的冲突面）

- `frontend/lib/hooks/use-sse-stream.ts` / `lib/chat/adapters.ts`：仅加 `parent_tool_call_id`
  分支与 child 行渲染（不触主题/布局/侧栏 — #2032 冲突面）。
- `app/agent_pi_bridge.py`：只改 Pi seam（abort 队列语义、turn 收尾 receipt evict 含 discovered）。
- `app/services/chat/execution_engine.py` / `app/api/routes/config.py`：W08 最小接线。
- ADR：新编号落 0288 起（0286/0287 已有重号；提交前再核对 master 最新）。
