"""F04 — Typed Context Assembly & Budget Governor (ADR-0208 D2 follow-up).

单一职责：把「给模型看什么、为什么看、预算多少」变成 typed、有界、
deterministic、可解释的组装管线。本包不是第二套 context truth —— 每个
provider 只投影既有单一真相（SessionPlan 信封 / map_state / MapSpec /
project_knowledge / project_memory / gis_memory / gis_context / situation），
本包拥有的是**组装纪律**：预算分配、去重、栅栏、receipt、governor 对账。

模块边界：
- ``contract``  — ContextItem / ContextProvider / TurnContextRequest 契约
- ``fence``     — 数据栅栏 + secret 双层消毒（不动 ``app/lib/redaction.py``，
                  该文件归 F09）
- ``dedupe``    — 确定性去重（内容指纹 + 权威版本优先）
- ``allocator`` — 分池预算分配（复用 ``context_budget.Category``/``plan_budget``）
- ``receipt``   — ContextAssemblyReceipt（digest + settle-once + 有界投影）
- ``governor_link`` — LLM context 维度 plan/settle（observe-first）
- ``providers`` — 既有 builder 的 typed 适配（零渲染逻辑复制）
- ``assembly``  — 编排（two-wave 并行收集 → 消毒 → 去重 → 分配 → 渲染）
- ``legacy_compat`` — 退役边界：旧拼接路径的字节等价兼容层
"""
