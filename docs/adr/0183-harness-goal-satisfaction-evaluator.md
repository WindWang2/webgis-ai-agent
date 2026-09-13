# ADR-0183: Harness-level Goal Satisfaction Evaluator

- 状态：Accepted（本 PR 落地 M0-M5）
- 日期：2026-09-14
- 关联：ADR-0081（完成契约）、ADR-0134（intent acceptance / continuation 接线）、ADR-0158（visual judge）、ADR-0159/0167（V11 质量基线与闭环）、docs/dev/harness-goal-evaluator-{recon,decisions,ledger}.md

## 背景

master 已有五层评价/裁决（7 维完成契约、Product Verdict、final map
verification、L5 visual judge、HarnessEvaluator 离线门），它们共同回答
「**地图产品**是否成立」。但「**用户的任务**是否真正完成」没有 owner：

- 用户要求「显示学校分布，比较各区，再导出 PPT 图」——地图 READY 而
  比较与导出缺位时，`_is_task_complete` 仍折叠为 True；
- 工具全部 200 但数据为空（empty_result）时，分析结果无人质疑；
- 多子目标的部分完成被整体 PASS 吞掉，无逐项 ledger。

并行线勘察（recon §4 / 附录 B）确认：8 个 open PR（#1270/#1273-#1279）
无一覆盖任务语义层；#1275/#1276/#1278 的契约中把 "Goal Evaluator" 列为
预设下游消费者，但它本身尚未存在。

## 决策

1. **新包 `app/services/gis_harness/goal_satisfaction/`**：契约
   （GoalRequirement / GoalContract / GoalEvidence）、确定性派生
   （intent / SessionPlan / map_product 结构化事实 → 需求面，不做
   raw-text regex）、证据注册（既有真相源只读投影，带
   source/revision/confidence/evidence_class）、纯函数评估器。
2. **不造第二 verdict**：复用 READY 族 / final_map / L5 visual 词表；
   visual/assisted 证据**永不单独背书 PASS**（fail-closed，
   `PASS_CAPABLE_CLASSES` 白名单强制）。
3. **单一评估点**：`maybe_finalize_map_product` → `map_product_block`
   内评估并持久化 additive 键 `map_product["goal_satisfaction"]`；
   其余一切面（SSE / LLM 投影行 / runtime 投影）只读存储块，不重算。
4. **advisory 信号，不抢权威**：`task_complete` 折叠语义不变（决策
   D-007）；goal 信号经三条 additive 通道消费 —— task_complete SSE
   载荷键、`[GIS Goal]` LLM 投影行（default Pi path 在收口前看见
   任务级 verdict）、`signal=replan` 路由既有 `request_replan`
   （预算治理不变）。
5. **反作弊语料先行**：`app/evaluation/goal_satisfaction_corpus.py`
   101 案例（G6 八大反事实注入为命名案例），`false_pass_rate == 0`
   由测试锁死为最高优先级指标。
6. **ADR 编号 0183**：0180（#1274/#1275/#1277 三方争用）、0181（#1276）、
   0182（#1278/#1279 双方争用）视为已消费。

## 评估层级（与既有层的对接，不重新编号）

```text
Execution evidence      → chapter 行状态 / tool receipts      (G2 证据)
Data sufficiency        → workflow_contract data_blockers /
                          product verdict 数据族码            (复用)
Spatial/GIS semantic    → scope/filter 一致性规则、
                          final_map / spatial validation      (复用+新规则)
Product completeness    → derive_product_verdict 七维契约      (只消费)
Cartographic quality    → V11 cartographic review 摘要         (只消费)
User-goal satisfaction  → GoalRequirement 逐项 ledger（本 ADR 新增层）
```

## 结果

- satisfied/partial/blocked/failed/not_evaluated 全局裁决 + 逐需求
  ledger + harness 信号（complete/continue/repair_cartography/replan/
  request_clarification/blocked_by_data）。
- 验收：101 案例 0 false-PASS；37 个 focused 测试 + 既有 120 个被接线
  面测试零漂移（见 PR body 的本地证据）。

## 风险与回滚

- 全部接线 additive：删除 `map_product["goal_satisfaction"]` 键写入
  （pipeline.py 一处）即整体回滚；投影面失败只少一行/一键。
- 风险：需求派生对 intent 字段的依赖（intent 缺失 → 契约 None → 既有
  行为）；capability 分类在 registry 缺席时退化为能力名模式（确定性）。

## 后续接口点

- #1278 `SkillEvidenceRecorder` → 证据注册的 skill 级投影源；
- #1275 `SituationDelta` → 情境事实（known/stale/unavailable）归因；
- #1276 `GoalRequirements` → 能力级需求子集对齐；
- #1277 `patch_plan` 协议 → replan 信号的执行通道；
- 前端 task_complete 芯片消费 `goal_satisfaction.verdict/signal`。
