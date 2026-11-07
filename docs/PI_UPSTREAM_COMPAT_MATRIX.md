# Pi 上游兼容矩阵（PI_UPSTREAM_COMPAT_MATRIX）

> G01-PI-NATIVE-RUNTIME / PI-10。当前 pin：**v1.5.0**（gitlink `f9a263655`，
> 上游 `earendil-works/pi`，原 `badlogic/pi-mono`）。
> 纪律：**不擅自升级**——升级需先更新本矩阵 + contract tests，走 feature-gated
> adapter（ADR-0282 语义）。

## 1. 依赖的 v1.5.0 契约面（本仓库消费的 API/协议）

| # | 上游契约 | 本仓库消费点 | 契约测试 |
|---|---|---|---|
| C1 | RPC wire 协议（request/response + 事件流，newline-delimited JSON） | `pi_rpc_client.py`（binary pipes、`_readline_bounded`） | `tests/integration/test_pi_vendor_contract.py`（活体） |
| C2 | 事件词表：`agent_start/turn_start/message_start/message_end/message_update/tool_execution_start/tool_execution_end/turn_end/agent_end/agent_settled` | `pi_event_mapper.py`（`agent_settled` 唯一终判；`agent_end.willRetry` 非终） | pi_doctor rpc 域（live 验证）+ `test_pi_event_mapper.py` |
| C3 | `prompt` 响应 `disposition` 字段（`handled` = input handler 消费，无 agent run） | `agent_pi_bridge`（send_failed 族诚实结算） | `test_pi_status_fail_closed.py` |
| C4 | delta-only `message_update` | `pi_event_mapper._extract_text_from_event`（快照语义 #816） | `test_pi_event_mapper.py` |
| C5 | pi-schema 参数校验（vendor 按注册 schema 校验工具调用参数） | `pi_native_surface` dump（`additionalProperties:false` 投影） | `test_pi_schema_coercion_golden.py`（真实 rpc-entry golden） |
| C6 | 扩展两段加载：`load()` 只注册、`session_start` 才可 `setActiveTools` | `index.mjs:260-291, 371-379` | pi_doctor extension 域 + `test_pi_extension_control_boundary.py` |
| C7 | `registerTool`（name/label/description/parameters/promptSnippet） | dump v2/v3 形状 | `test_pi_dynamic_surface.py` |
| C8 | `--no-builtin-tools` / `--no-session` / `--mode rpc` / `--extension` CLI | `pi_rpc_client._spawn_process` | pi_doctor build/env 域 |
| C9 | `set_model` RPC（provider/modelId 需已注册） | `pi_rpc_client.start`（可选） | pi_doctor rpc 域（环境内） |
| C10 | abort RPC（会话路由、pending tool 快照语义） | `PiBridge.abort`（CONC-F1 快照） | `test_pi_cancellation_unified.py` |

## 2. 引擎/环境下限

- node ≥ 22.19（vendor engines；本仓 pi_doctor build 域检查）
- Python 3.13（`pi_rpc_client` 类型注解 `X | None` 等）
- 上游 floor：`PI_MIN_VENDOR_TAG = v1.5.0`（`pi_compat`；floor 违反 → WARN +
  pi_doctor vendor 域 FAIL）

## 3. 升级评估清单（未来版本 X）

1. `git submodule` 切新 tag → `bash scripts/build-vendor-pi.sh`。
2. `python scripts/pi_doctor.py`（rpc 域 live 验证事件词表与 `agent_settled`
   终判是否漂移）。
3. `python -m pytest tests/integration/test_pi_vendor_contract.py
   tests/integration/test_pi_schema_coercion_golden.py`（wire 契约 + 强转 golden）。
4. `PI_JOURNEY_E2E=1 ... test_pi_stub_llm_journeys.py`（七段全栈旅程）。
5. 对照 C1–C10 逐项复核：事件词表增删、`disposition` 语义、扩展加载阶段、
   registerTool 形状、CLI flags、abort 快照。
6. **禁止**：在新版发布证据（tag/release notes + 上述测试绿）之前引入未发布
   API；禁止跳过矩阵直接升 pin。

## 4. 已知不升级的理由（2026-11-07 裁决）

- v1.5.0 为已验证稳定发布版（pi_doctor 6 域 PASS + 全部契约测试绿）。
- 本方向的缺口（状态机碎片化/幂等/三态披露等）均在宿主侧，不依赖上游变更。
- 上游发生破坏性变更时的回退：gitlink 回 pin v1.5.0 + 重跑 build-vendor（幂等）。

## 5. 版本历史

| pin | 引入 | 备注 |
|---|---|---|
| v0.81.1+1 | PR #1973 前 | 旧基线 |
| **v1.5.0**（当前） | PR #1973（ADR-0282） | 两段加载/prompt disposition/pi-schema 校验等契约面以此为准 |
