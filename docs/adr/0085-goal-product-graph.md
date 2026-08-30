# ADR-0085: Goal → Product Graph（目标到产品 facets 的结构化投影）

- Status: accepted
- Date: 2026-08-30
- Extends: ADR-0076（SessionPlan 单一计划真相）、ADR-0081（产品完成度）、ADR-0082（产物血缘）

## Context

SessionPlan / PlanGraph 围绕 **capability** 组织 —— 这对执行是对的，但对
「用户目标 → 地图产品」的语义是间接的：『成都小学分布情况』的完整答案是
facets 的集合（空间分布热力 + 行政区对比 choropleth + 统计面板 + 图表 +
叙述），而计划视图里它们只是几行能力行与图层声明，Pi 看不见"产品构成"，
也看不见哪个 facet 还欠着。

## Problem

需要一个 Goal → ProductGraph 层，同时不得违反单一事实源：

- ProductGraph 不得成为新的持久计划真相（ADR-0076 的 SessionPlan 不可
  出现竞争者）；
- 不得让 SessionPlan 状态与 ProductGraph 状态各自独立推进（双状态漂移）。

## Decision

`app/services/gis_harness/product_graph.py`：**派生只读投影**（纯函数，
每次读取时计算，绝不持久化）。

1. **节点**（有限集合）：`map_layer`（章节计划层 → MapSpec 在场/启用
   投影）、`analysis`（能力行）、`statistics / chart / annotation`
   （MapSpec 组件）、`export`（模板导出画像，信息性）、`narrative`
   （Pi 叙述 —— 完成块的代理：map_product=complete 即 done）。
2. **边**：analysis → map_layer 供给边以 MapSpec layer provenance 的
   result_ref == 行 bound_ref **实证**（实例级血缘，与 ADR-0082 的
   artifact 图同源）；无 spec 时退化为"已完成分析 → primary 层"兜底。
3. **状态全部回读既有事实**：行状态 / 图层在场与启用 / 组件 enabled /
   map_product 块 —— 投影不产生新状态机。
4. **披露**：有界单行 `[Products] map 2/2 · stats 1/1 · chart 1/1 —
   1 owed` 追加进 [GIS Plan] 投影（`format_session_plan_projection` 增
   只读 mapspec 参数，由 async 调用方 bind_turn_prompt 拉取）。

## Alternatives

- **ProductGraph 持久化为 gis_chapter 字段**：拒绝 —— 派生数据持久化即
  第二真相（漂移窗口 = 每次未同步的突变）。
- **per-product 完成度（每 facet 一个 MapCompletionResult）**：本期拒绝 —
  需要 per-facet bbox/validator 拆分，是 ADR-0081 的后续演进（见
  Future work）；当前完成度仍是单地图合成级。
- **Pi 侧自行推断产品构成**：拒绝 —— LLM 从行列表推断构成不可靠且不可
  测试；确定性投影一行即达。

## Trade-offs

- 每次读取重算（O(章节行 + spec 组件)）—— 毫秒级、纯内存，换取零漂移。
- 无 MapSpec 时图层 facet 状态退化为 pending（在场不可知）—— 诚实优先，
  不虚构 done。

## Compatibility

- SessionPlan schema 零变化；投影函数新增可选参数（缺省行为不变）。
- 旧章节（无 map_layers/组件）→ 空图 + 空 summary 行，零噪声。

## Performance

投影 O(N+C)；在 turn-context 组装路径上多一次 MapSpec 读取（既有
read-side，无锁）。

## Failure semantics

投影失败（异常/坏章节）→ 少一行披露，绝不阻断 turn 上下文。

## Migration

无迁移。

## Future work

- per-facet 完成度与 per-facet result_bbox（ADR-0081 演进）；
- ProductGraph 驱动 Pi 的"下一步产品动作"建议（欠的 facet → 对应能力）；
- 与 ADR-0082 artifact_dependency_report 的血缘并轨。
