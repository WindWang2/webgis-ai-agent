# DECISIONS — GOAL-20261127-01

> 技术分歧裁决记录（按最小变更、复用权威、可复现、安全、真实用户路径排序）

## D1（定稿）：provider 失败观测 = 双通道分层

- **实测事实**（子代理 A 逐行 vendor 源码）：`after_provider_response` 在
  RPC stdout 协议上**不存在**（rpc-mode.ts 只转发 AgentSessionEvent/response/
  extension_ui_request/extension_error），扩展 handler 唯一出口是 stderr
  （index.mjs:607-618）；但它对**每次 provider HTTP 响应（含每次重试）**
  触发（vendor sdk.ts:379-387），带精确 status + retry-after —— 是路由健康
  信号的最佳来源，目前降级为运维日志。
- 裁决：
  1. **主通道（每 attempt 精确信号）**：extension 的 stderr 行升级为结构化
     可解析格式（`[webgis-tools][provider-obs] status=NNN retry_after=X`），
     `pi_rpc_client._read_stderr` 识别后写入新增的 provider-observation sink
     （复用 `set_ui_request_sink` 同款注入模式）→ 桥消费 → 健康表 record_failure。
     不新增 HTTP 面/鉴权面。
  2. **辅助通道（turn 级结构化事件）**：`auto_retry_start/end` 事件已在
     pi_event_mapper 稳定消费；桥侧增加 attempt 计数与终态失败分类回写。
- 理由：复用既有通道与注入模式，最小变更；精确 status 进健康表（429 vs
  5xx vs 401 分类决定 cooldown/deprioritize/不重试），auto_retry 事件补
  attempt 计数语义。

## D2：Pi inherit 路径接 ModelRouter，用户显式选择优先级不变

- session preference 显式 → 行为与现状一致（目录重验证+能力门，撤销/不可用
  → typed 拒绝，绝不静默换模型）。
- inherit → 经 ModelRouter.route（require_tools=True，GIS 宿主主对话）：
  主模型仍是 resolve_llm_config 结果；健康冷却/能力不匹配 → 沿既有 fallback
  语义留痕降级。reason codes 进有界 route receipt。
- 理由：复用路由权威（G1 缺口闭合）；用户控制面不变。

## D3：attempt 链关联 = ChatTurnRuntime additive 增列 + 既有 join，不建新表

- turn→usage：既有 (turn_id, call_seq)；turn→job：既有 JobOrigin(session/turn/
  tool_call)；turn→attempt：本线新增有界列（attempts 计数 + route reason codes +
  最后失败分类），单 migration 单 head。
- 理由：不重复建设账本；关联链可串证即可。

## D4（定稿）：typed indeterminate 已有承载，不新增词表

- 实测：`turn_receipt_lifecycle.mark_unknown`（G43 C21）+ TurnContextReceiptRow
  状态机（assembled/not_sent/submitted/unknown）+ SSE resume「never start
  another execution」声明已构成 indeterminate 语义的诚实承载。
- 处置：S3 只做投影面消费（把 unknown/submitted-but-unsettled 的 turn 在
  运行事实投影里如实呈现 + 用户显式继续操作），不改终态词表。

## D6（新增）：get_session_stats 作为账本对账锚点

- 实测：vendor `get_session_stats` RPC（agent-session.ts:4369-4412）宿主从未
  调用；usage_ledger docstring 自述「对账走 Pi session_stats 快照」但无实现。
- 裁决：S3 在会话 usage 投影中加有界 vendor 对账块（调用现成 RPC，不建新账本），
  把 not_included（compaction/辅助调用）的差额如实呈现为 vendor 视角 vs
  宿主账本视角两列。

## D7（新增）：路由候选必须与管理员目录求交（授权边界不可逃逸）

- catalog = 授权边界（session_catalog.py 头注释「descriptor ≠ 授权」）。
  路由降级链候选集 = 可用目录项；绝不因模型在描述符注册表/角色 fallbacks
  里就允许 set_model（vendor 会拒，但宿主不得先造越权请求）。
- 实现取向：RouteRequest 增加可选 `candidate_models`（additive，缺省 = 现行为
  不变），Pi strategy 用目录 id 列表传入；router 语义（硬资格+健康排序+理由码）
  原样复用。

## D5：v1.6.1 gitlink 不动

- ls-remote 实证上游最新 tag = v1.6.1 = vendor pin。无新正式版 → WP5 只做
  兼容矩阵核对与回归，不改 vendor。
