"""Spatial Events — 事件驱动空间操作控制平面（schema v1）。

层（全部单向依赖，禁止反向 import）：
- ``contracts``：信封/watch/trigger 类型与封闭词表
- ``flags``：分层 kill-switch（生产默认全关）
- ``ledger``：durable 事件账本 + 游标 + watch 存储
- ``watch``：watch 求值纯函数引擎
- ``situation_projection``：event → Situation 事实投影（先投影再决策）
- ``mission_bridge``：投影事实 → Mission create/revise/resume（复用 CAS/lease）
- ``invalidation_bridge``：版本变化 → 受影响子图/evidence 失效
- ``governor_gate``：trigger 副作用的 Resource Governor 背压门
- ``service``：ingest/drain 组合面（worker 循环）
- ``adapters``：内部事件生产 hooks（map mutation/artifact/job/mapproduct）
- ``webhook``：外部事件安全 seam（HMAC）
"""
