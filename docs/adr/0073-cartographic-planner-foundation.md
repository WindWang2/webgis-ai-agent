# ADR-0073: Cartographic Planner 基础 —— 分布驱动分类裁决、意图投影与 VisualizationPlan

日期: 2026-08-27
状态: accepted

## 背景

审计（Reviewer D）判定制图管线介于"能画"与"知道为什么画"之间：
- `CLASSIFICATION_METHODS`（best_for/caveat/authority）与
  `MapModel.recommended_classifiers` 是**文档性元数据，无任何代码消费**——
  分类方法由调用方默认 `quantiles` 或模板 payload 硬编码；
- `layer.cartographic_intent.expected_visible` 被 QA（RESULT_VISIBILITY）读取
  但**全仓无生产者**——意图从未投影到图层，检查恒 not_evaluated；
- "为什么这样画"散在 plan.evidence dict，非一等工件。

## 决策

`app/lib/cartography/visualization_plan.py`（纯函数）：

1. **`choose_classification(stats, ...)`** —— 分布证据（n/min/max/mean/median）
   × 知识库裁决：重尾（mean ≥ 1.5×median，**相对**偏度——极端离群值会稀释
   range 型偏度，恰恰漏掉 head_tail 要捕捉的形态）→ head_tail；近均匀
   （|mean−median| < 值域 2%）→ equal_interval/quantiles；默认 natural_breaks。
   显式指定优先；k 夹 [3,7]。每个选择携带 reasons / rejected-with-caveats /
   文献 authority。
2. **`build_visualization_plan`** —— VisualizationPlan 一等工件
   （intent → map_model → classification → palette → composition，每步
   choice+reason），供 QA 反查与项目记忆指纹固化。
3. **意图投影** —— UpsertLayerIntent 提交时落 `cartographic_intent`
   （expected_visible = authoring 可见性；role 透传 context_role）；
   `PatchLayerPresentationIntent` 改写 expected_visible（显隐是显式决策）。
   调用方显式给出的 intent 优先。
4. **`create_thematic_map`** —— `method` 默认 None（原硬编码 quantiles）：
   缺省时由裁决器按字段分布决定，`classification_plan` 证据随结果下发。

## 后果

- RESULT_VISIBILITY 从恒 not_evaluated 变为可评："故意隐藏"（用户/agent 决策
  → expected_visible=False → pass）与"结果层被误藏"（expected_visible=True +
  hidden → fail + auto_safe 修复）可区分；与 user-wins 一致（用户隐藏改写意图，
  不会触发修复对抗）。
- 分布驱动只在**有成图数据时**发生（planner 在数据获取前运行，分布裁决属于
  authoring 阶段）——planner 的数据需求规划（DataRequirement）是下一阶段工作。
- 重尾检测的偏度阈值（0.5）与近均匀阈值（2%）是可调启发式，测试锁定行为。
