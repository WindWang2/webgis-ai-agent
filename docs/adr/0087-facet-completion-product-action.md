# ADR-0087: Per-Facet Product Completion 与 Product Action Advisor

- 状态：Accepted（2026-08-30）
- 关联：ADR-0085（Goal → Product Graph —— 本 ADR 落地其 Future work）、ADR-0081（Map Product Completion Runtime）、ADR-0086（Render Observation Runtime）、ADR-0076（SessionPlan 单一计划真相）
- 分支：`feat/gis-render-observed-product-closure`

## 1. Context

ADR-0085 建立了 Goal → ProductGraph 派生投影，但完成度仍是合成级
（`map_product` 一个块、`[Products] map 2/2 · chart 0/1` 一行计数）：

- 看不见"哪个 facet 欠着、欠在哪一层"（artifact? MapSpec? 渲染?）；
- 没有从产品构成反推下一步动作的确定性通道 —— Pi 只能从行列表自行推断；
- per-facet result_bbox 在 ADR-0085 中明确为 Future work。

## 2. Problem

把整图级 completion 演化为 facet 级 completion，并从欠账 facet 推导下一
GIS action —— 同时不得建立独立推进的 facet 状态机、不得让 advisor 形成
第二 agent loop、不得破坏既有 `map_product` 兼容性。

## 3. Decision

### 3.1 ProductFacetCompletion（派生投影，`product_graph.py`）

```text
ProductFacetCompletion {
  facet_id / kind / key / label
  status: complete | pending | failed | needs_repair | off
  required
  capability_ids[]      # registry capability（map_layer ← 计划行
                        #   source_capability；analysis ← 行能力）
  artifact_ref / layer_ids[] / component_ids[]
  bbox                  # descriptor bbox → spec source bounds → null（不虚构）
  render_status         # verified | issues | ""（无证据不虚构）
}
```

- `build_facet_completion(chapter, mapspec, *, descriptors, observation)`
  纯函数：输入全是既有事实（行状态 / 图层在场启用 / 组件 enabled /
  map_product 块 / RenderObservation / ref descriptor）；零持久化；
- 渲染证据只在 observation 匹配当前 revision 时参与（ADR-0086 防护在
  投影层同样生效）：层挂载缺席 → facet `needs_repair` + `render_status:
  issues`；无证据 → `render_status: ""`，状态退回 desired-state 投影；
- chart 必需信号：模板 `export_profile.chart=True` 且无 enabled chart
  组件 → ProductGraph 合成 `chart:required` pending 节点 —— 产品图反映
  **应然构成**（派生自章节 + MapSpec，仍零新状态）。

### 3.2 ProductActionAdvisor（纯函数，`product_action.py`）

- `advise_next_product_action(chapter, facets)`：确定性 / 只读 / 零 LLM /
  零 IO；输出 `ProductActionRecommendation { facet_id, kind, action,
  capability, reason }`；
- 确定性优先级：execution blocked（failed 行 → retry_analysis）→ pending
  analysis（run_analysis）→ pending map_layer（produce_layer，capability =
  计划层 source_capability）→ render 缺席层（repair_layer_render，无
  capability 映射）→ chart owed（produce_chart）→ statistics →
  narrative（finalize_product）；
- **capability 字段只在 registry 能力真实存在时填写**：chart 等产出通道
  是 harness 工具族而非 capability —— 如实留空，绝不把 tool id 冒充
  capability（Product Action → Capability → Algorithm Resolver → Tool
  的分层不被 shortcut）；
- 投影：`[Next GIS Action] <capability|kind:action>` 单行追加进
  [GIS Plan] 块尾部（无欠账 → 零噪声）；`[Products]` 行升级为点名欠账
  kind（`— chart owed`）。

### 3.3 不变量

- Pi 仍是唯一 Agent Host；advisor **不是 agent loop** —— 只向 Pi/harness
  暴露"欠什么、经哪个 capability 补"，执行仍走既有 Pi + harness 通道；
- Facet status 从既有事实派生，无独立状态机、无持久化（复算即得）；
- `map_product` 块 / projection 行语义不变（旧消费方零感知）。

## 4. Alternatives

- **facet 状态持久化 + 独立推进状态机**：拒绝 —— 第二计划真相（ADR-0076），
  且与行状态必然漂移；
- **advisor 直接选 tool**（ProductGraph → heatmap_data tool）：拒绝 ——
  绕开 Capability → Algorithm Resolver 分层（P8 rationalization 的成果）；
- **advisor 自循环调用工具直到 facets 补齐**：拒绝 —— 第二 agent loop；
  Pi 拥有 turn 主导权，advisor 只是投影；
- **把 render 证据塞进 ProductGraph 节点而非独立 facet 结构**：拒绝 ——
  节点是产品构成投影，facet completion 是完成度投影，输入证据面不同
  （descriptors / observation 不进 graph 基础投影，保持其零 IO 特性）。

## 5. Trade-offs

- 每次 turn-context 组装多一次 O(章节行 + spec 组件) 纯内存投影（毫秒级）；
- facet render 证据依赖前端在线 —— 离线/旧客户端 facet 完成度退化为
  desired-state 投影（诚实降级，不虚构 verified）。

## 6. Compatibility

- `[Products]` 行格式向后兼容（计数段不变；尾巴从 `N owed` 细化为点名，
  既有断言只检查 `[Products]` / `owed` 子串）；
- `build_product_graph` / `summary_line` 签名不变；facet completion 是
  新增 API，无行为替换。

## 7. Performance

- 投影 O(N + C) 纯内存；advisor O(facets)；无 IO、无 LLM；
- bbox 全部来自既有 descriptor / source bounds 元数据 —— 不扫描
  FeatureCollection（150k features 与 100 features 同成本）。

## 8. Failure semantics

- 投影异常 → 少一行披露（turn 上下文绝不阻断）；
- 输入缺失（无 spec / 无 descriptors / 无 observation）→ 对应字段退化为
  pending / null / ""（不虚构事实）。

## 9. Migration

无迁移。`map_product` 块与 SessionPlan schema 零变化。

## 10. Future work

- facet 级导出 parity（per-facet export 支持矩阵）；
- narrative facet 的确定性内容证据（当前以 map_product 块为代理）；
- advisor 与 plan_graph `recommended_next` 的并轨（执行侧/产品侧建议合一）。
