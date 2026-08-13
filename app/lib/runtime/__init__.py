"""统一运行时证据链（Runtime Observability）。

一次 Agent turn 的真实执行跨越多个 asyncio.Task 与多个 HTTP 请求：

    POST /chat/stream      → stream_task（SSE 流，Pi 子进程 / legacy 引擎）
    POST /pi-tools/execute → dispatch_task（Pi 回调，单独请求/单独 task）
    POST .../map-action-ack → ack_task（前端 ACK，单独请求/单独 task）
    Celery worker          → 另一个进程

本包提供贯穿这些边界的「轻量」关联与证据原语，使系统能回答：

  - 一次 turn 慢在哪里（context / LLM / tool / map_ack 各阶段耗时）
  - 失败在哪里（provider_error / tool_error / cancelled / superseded …，不坍缩成
    单一 "tool execution failed"）
  - 做了多少无效工作（重复工具调用、重试、孤儿 map 动作）
  - 哪里出现资源/性能异常（客户端创建数、序列化次数、队列长度…）

设计约束（与 /goal §5/§11/§14 一致）：

  * 不引入重型 tracing 平台；只提供小而统一的 evidence envelope。
  * 关联走 ContextVar（``RuntimeContext``）+ turn_id 键控的进程内注册表
    （``TurnEvidenceRegistry``），绝不用 module-global mutable "current request id"。
  * missing evidence ≠ success：无证据的指标输出 null/0.0，绝不输出 100.0。
  * 取消不算失败；终态恰好一次。
  * 不记录 API key / Authorization / 完整 prompt / 原始 GeoJSON（复用现有 redactor）。
  * 不复制 durable state 作为第二套 source of truth。
  * 单进程假设：注册表与 Pi bridge 单例都是进程内的；workers>1 时 Pi 回调可能落到
    非归属 worker，turn 关联回退为空（graceful，记 warning，不崩）。

模块：
  * ``context``  — RuntimeContext（request/session/turn/run）ContextVar 主干。
  * ``evidence`` — Outcome / OutcomeRecorder / TurnEvidence / TurnEvidenceRegistry。
"""
from __future__ import annotations
