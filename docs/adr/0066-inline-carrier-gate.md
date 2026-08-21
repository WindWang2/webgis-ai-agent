# Inline 载体门：>5000 特征拒绝，ref 是大数据集的唯一合法形态，无逃生舱

Date: 2026-08-21

## Status

Accepted

## Context

#687 之前，MapSpec source 允许任意大小的 inline GeoJSON（`inlineData`）——12MB spec × 50 次编辑 ≈ 1GB 累积磁盘写，且每次变更的提交面（序列化/深比较/checkpoint）都是 O(payload)。#669 的 CoW 解耦了内存变更成本，但持久化平面的成本仍与 payload 同阶。修复选择了**在源头拒绝**：`mapspec_source.store_data` 对 >5000 特征的内联载体 fail-loud（`INLINE_FEATURE_LIMIT`），指引走 `ref:` 引用——大结果集本就由 dispatch 自动 Ref 化，手搭 spec 不该内联大载荷。

被否决的备选：
- **静默转 ref**：转换需要会话上下文（store 归属），且会掩盖调用方未走 ref 的事实——错误在源头可见比在下游可诊断便宜。
- **阈值参数化/调高**：阈值（5000）不是重点，**拒绝行为**才是——任何"可调"都会被调成"事实上无门"。
- **测试逃生舱**（env 开关）：nightly heavy matrix 的 e2e 基准曾因此门失败（手搭 50k 内联），但修法是基准改走 ref 路径（生产形态）而非给门开洞。逃生舱存在即会被滥用，且"门可绕过"本身要写进所有文档。

## Decision

1. `INLINE_FEATURE_LIMIT = 5000`：超过的内联 GeoJSON 载体在 `mapspec_source.store_data` 处 raise，**绝不静默转换、绝无 env/参数逃生舱**。
2. 大数据集的唯一合法载体是 session ref（dispatch 自动 Ref 化大结果；手搭 spec 用 `{"ref_id": ...}` 元数据载体）。
3. 测试/基准需要大载荷时走 ref 路径（见 `test_perf_mapspec_e2e.py` 的 #687 followup 改写）；撞门即说明测试在用不该用的形态。
4. 平台边界如实记录：CPython json 编解码持 GIL，单巨字段的解析/序列化无法靠线程封顶循环停顿——这是门存在的根本理由之一，不是实现缺陷。

## Consequences

- 手搭大内联 spec 的旧代码/旧测试会在门处显式失败（nightly 首跑即抓到 e2e 基准一例，已改写）。
- ref 路径成为大载荷的唯一路径后，提交面成本与 spec 字节数解耦的承诺才成立。
