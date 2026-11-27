# DECISIONS — GOAL-20261127-01

> 技术分歧裁决记录（按最小变更、复用权威、可复现、安全、真实用户路径排序）

## D1：provider 失败回写健康表的通道 = RPC 事件（auto_retry_*）优先，extension 观测为辅

- 备选：(a) 扩展 after_provider_response 经新 HTTP 回调通知宿主；(b) stderr 结构化行
  升级解析；(c) 复用已通过 RPC drain 到达宿主的 auto_retry_start/end 事件。
- 裁决：**先实测** WP1 fixture 中 (c) 的可达性与可靠性；auto_retry 事件已
  在 pi_event_mapper 稳定消费（可靠通道、有 attempt/maxAttempts/errorMessage/
  finalError），after_provider_response 目前仅 extension 内部（stderr）。
  若 (c) 足够覆盖用户旅程（429→等待→恢复→不重复副作用），则不新增 HTTP 面；
  extension 观测仅补 stderr 有界环（现状保留）。
- 理由：最小变更 + 不新增鉴权面 + 复用既有通道。待 S1 实测后定稿。

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

## D4：typed indeterminate 语义落在 turn 终态词表扩展（若现状缺失）

- 待 S1 核实 event_resume/settle 现状后定（TurnEvidence.Outcome 词表、
  ChatTurnRuntime.status 词表）。

## D5：v1.6.1 gitlink 不动

- ls-remote 实证上游最新 tag = v1.6.1 = vendor pin。无新正式版 → WP5 只做
  兼容矩阵核对与回归，不改 vendor。
