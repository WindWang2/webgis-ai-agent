# ADR-0167: V11 W7 — 闭环与自愈智能化（本地视觉判据、自愈策略库与归因、动作空间扩展、阻断切换）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/v11-master（W7）
- 关联: ADR-0158（视觉裁判与自愈闭环）、ADR-0160（not_evaluated 化解表——VISUAL_OVERLAP→W7）、ADR-0161（反馈信号共用存储）、ADR-0166（W6 交付）

## 1. 背景与靶心

缺口 G6：L5 ``goal_satisfaction`` 因 VLM 未配恒 ``not_evaluated``（fail-closed
空转）；G10 半边：``selfheal_actions`` 接线仅 1 处、无成功率学习、无效果归因。
W7 交付「无 provider 也有确定性视觉证据 + 自愈会挑动作」的闭环智能化。

## 2. 决策一：本地确定性视觉判据（W7.1，G6 的 fallback 半边）

- ``app/lib/harness/local_visual_criteria.py``：从渲染 PNG 直接测量的五维
  确定性事实 —— 墨量（readability）/ 边缘密度（information_density）/
  视觉重心偏移（composition_balance）/ 覆盖率（polish_completeness）/
  显著色桶数（color_discriminability）。零网络、零随机、同图恒同分；
  复用 golden_diff 的 ``decode_png``（同源工具）。
- **边界诚实**：本地判据不替代 VLM 的语义审美；无画面 → 逐维
  ``not_evaluated``（fail-closed 保持——「无证据不通过」总纪律不变）。
- 阈值首轮 **provisional**（W8 实测分布校准前不拦截，§0.5）；判定词汇与
  selfheal 的 ``VISUAL_*`` trigger 对齐（可直供动作选择）。

## 3. 决策二：自愈策略库与归因表（W7.2/W7.3）

- ``app/services/cartography/selfheal_policy.py``：全部落
  **W1 的 ``carto_feedback_signals``**（共用存储兑付）——
  ``target="selfheal:{action}"``、``repair_success/repair_failure``、
  ``payload={context, expected_effect, actual_delta, surface, risk}``。
- **成功率表**：按动作聚合（计数 + 半衰期衰减有效权重），排序契约
  （成功率降序 → 样本数降序 → action_id）确定性。
- **归因表**：逐条 (expected, actual) 判定（``effective`` = actual ≥
  expected×0.5 保守半量线；``ineffective`` 即降权信号源）；验收要求
  ≥30 条真实样本（测试以 30 条真实记账验证）。
- **先验只重排**（``rank_actions_by_history``：不增删候选；冷启动保持
  原序）——与 W1 recipe_affinity 同纪律。

## 4. 决策三：动作空间扩展（W7.4）

- 注册表从 V10 的 10 个动作扩到 **14 个**：新增
  ``switch_composition``（换版面，取 W5 备选组合的次优候选，语义风险）、
  ``change_aggregation``（换聚合粒度，语义风险）、
  ``change_projection``（换投影/范围，语义风险）、
  ``resample``（重采样，呈现级 auto_safe）。
- trigger 词汇沿用既有单点（fail 规则 ∪ ``carto.*`` ∪ ``VISUAL_*``）；
  排序契约不变（risk 升序 → expected_effect 降序 → id）。

## 5. 决策四：record-only → 阻断切换（W7.5）

- ``selfheal_blocking_enabled()``：``CARTO_SELFHEAL_BLOCKING=1`` 才阻断；
  **默认关 = V10 record-only 语义逐字节不变**。
- **切换条件**：W8 基线稳定（任务书原文）；切换记录于本 ADR；**一键回滚
  = 清环境变量**（测试锁定 off→on→off 往返）。
- 阻断语义的消费者（提交前拦截未修复 fail 级议题）随 W8 基线批接线；
  本波交付开关本体与回滚纪律。

## 6. 验收对照

| 任务书 W7 验收 | 状态 |
|---|---|
| visual 类证据在 ≥5 类图型产出非 not_evaluated（provider 可用时） | ⚠️ 本地判据通道交付（五维可评估）；VLM provider 配置与 ≥5 图型端到端归 W8 矩阵（无 key 环境据实登记） |
| 失败/无 provider 时 fail-closed 断言通过 | ✅（no_screenshot → not_evaluated 断言） |
| 自愈动作成功率表可查询 | ✅ action_success_table |
| 归因表有 ≥30 条真实样本 | ✅ 测试 30 条真实记账 |
| 阻断切换可回滚 | ✅ env 往返断言 |

## 7. 风险与回滚

- 本地判据只增证据不松纪律（无画面仍 not_evaluated）；阈值 provisional。
- 自愈学习只重排不改裁决面；记账 fail-safe。
- 回滚点：tag `ac-v11-w7`。
