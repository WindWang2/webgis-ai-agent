# 0288. Pi v1.5.1 升级：原生工具发现、嵌套调用与运行时生命周期契约

**Date:** 2026-11-10
**Status:** Proposed
**Branch:** `zcode/goal-g12-pi-native-integration-20261110T133034Z`
**Baselines:** ADR-0282（v1.5.0 兼容契约）· ADR-0103（动态工具表面）· ADR-0224（PiToolSpecV1/SurfaceReceipt）· ADR-0286（TurnPhase/三态披露）· ADR-0231（wrap vendored Pi）

## Context

G12 之前，Pi 宿主的模型可见工具面是「冻结 7 前门 + per-turn setActiveTools 激活的
动态候选 + webgis_execute 长尾代理」。长尾工具对模型不可发现：模型要么靠
`list_available_tools` 两跳（先列名再经代理），要么把请求塞进 proxy —— 无法按需
把一个真实注册工具的完整 schema 声明进下一模型请求。同时：嵌套（工具内工具）调用、
clear_queue/有序停机、模型配置真实生效（#2061）、compaction 生命周期均未接入。

上游 v1.5.1（`dd30101c`，2026-11-10 stable）恰好交付了这组能力的协议面：
`tool_search`（BM25 + `Intl.Segmenter` 中文分词 + 本地 `$ref/$defs` 解析进检索语料，
命中 **merge** 进 active 集）、`codemode`（QuickJS 沙箱脚本经 `ctx.executeTool` 有界
嵌套调用）、`tool_execution_*` 事件携带 `parentToolCallId`、`clear_queue` RPC、
有序停机（首信号清理可数秒、第二信号强退）。已消费协议文档
（rpc-commands/json/rpc-extension-ui）与 v1.5.0 **逐字节一致**（C1–C10 全保持）。
v1.5.2（快照后 5h 发布）的 coding-agent 集成面与 v1.5.1 零 diff，无本任务所需
能力且无浸泡 → 不升，记入矩阵为 pin-only 后续变更。

## Decision

1. **pin 升级到 v1.5.1**（`dd30101c`），`PI_MIN_VENDOR_TAG` 同步。契约面新增
   C11–C16（见 `docs/PI_UPSTREAM_COMPAT_MATRIX.md` §1）：内置扩展在
   `--no-builtin-tools` 下仍加载、tool_search merge 语义、嵌套 parentToolCallId
   事件、clear_queue/停机语义、原生 exposure 字段、根 lockfile→npm ci。
   回退 = gitlink 回 v1.5.0 + rebuild；G12 新行为全部有 env kill-switch。
2. **原生工具封套（dump v4）**：spawn dump 在 v3 上加性新增 per-tool
   `exposure`（native 前门 + proxy = `direct`；长尾超集 = `deferred`）、
   `namespace`（有界域分组）、`annotations`（side_effect 派生的 MCP 语义提示 ——
   **提示不是授权**，执行权威仍在 dispatcher 闸）。指纹口径不变：exposure 是
   投影决策，不入 PiToolSpecV1 指纹。
3. **发现入口常驻**：扩展常驻激活集纳入上游内置 `tool_search` 与 `codemode`
   （`exposure:"model-only"`，仅模型可直调，脚本不可触达），`PI_NATIVE_DISCOVERY=0`
   一键回退今日行为。发现只改**声明**：新激活工具仍必须落在 spawn 注册超集
   （model_visible ∧ tier<3 ∧ 非 DESTRUCTIVE ∧ schema 在预算内）内，执行仍经
   `/pi-tools/execute` → bridge secret + turn token + live-turn + dispatcher 全链。
4. **discovery grant（授权一致性）**：`pi_surface_receipt` 增加 turn 内 additive、
   幂等、有界的 `discovered` 记录；`enforce_callback_scope` 判定集从
   `selected` 扩为 `selected ∪ discovered`，且 `PI_SURFACE_SCOPE_ENFORCE` 默认
   改 ON（回退开关保留）。授权集从不因发现扩大：发现的直调 ≈ 代理调用的权限等价
   （同一注册超集 + 同一闸序），发现的只是「声明面」。
5. **嵌套调用投影**：`tool_execution_*` 事件携带 `parentToolCallId` 时映射为
   child step（SSE payload 加性 `parent_tool_call_id` 字段），结果缓存按嵌套
   toolCallId（`<parent>/<n>`）会合；父工具结果携带 bounded nested 摘要；
   终态仍由 `agent_settled` 唯一裁决，重复 child end 不重复展示/结算。
6. **完全停止语义**：用户级「停止任务」= `clear_queue` → `abort`（上游 Esc 语义）；
   steer/follow-up 排队消息不再自行重启。停机窗口对齐上游：stdin EOF → 有界等待
   （`PI_GRACEFUL_STOP_TIMEOUT_S`，默认 8s，覆盖 dispose 的 tool settlement 与
   credential writes）→ terminate → 有界等待 → kill → 收尸；绝不双发信号截断清理。
7. **模型配置生效（#2061）**：Pi spawn 的 models.json/settings.json 改由
   `model_config.resolve_llm_config()`（Settings ⊕ 运行时 override 的单一解析点）
   派生；`/config/llm` 更新后按配置代际（不可逆指纹）标记 worker 待换代，新 turn
   用新代、当前 turn 不切半套、失败保留旧代并如实披露「未生效」。生产禁动态改
   配置的既有约束不放松。无 reload RPC —— 代际更替靠进程换代，不编造命令。
8. **compaction/恢复**：扩展挂 `session_before_compact`/`session_after_compact`
   仅记账披露；GIS 状态重建走 typed assembly 的有界 versioned provider（引用
   SessionPlan/Artifact/MapSpec 权威源，不凭 summary 授予修改权，不重放副作用）。

## Consequences

- 模型可用自然语言（含中文、$ref 内属性描述）发现长尾真实工具并直调 —— 不再
  依赖 list_available_tools 两跳或 proxy 包装；声明面前后变化有旅程证据。
- codemode 让「对多图层分别摘要并比较」类只读组合在有界沙箱内并行完成；bash/
  read/write/edit 因从未注册而不可达（A7），未授权能力坦诚缺失。
- `PI_SURFACE_SCOPE_ENFORCE` 默认 ON 后，面外裸名直调被拒 —— 但发现路径合法、
  proxy 通道语义不变；旧行为可用 env 回退。
- 升级/回退是 gitlink-only 操作；每次移动 pin 仍须跑契约车道（矩阵 §3）。
