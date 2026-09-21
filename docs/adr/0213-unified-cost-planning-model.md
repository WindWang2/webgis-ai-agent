# ADR-0213: Unified Cost / Resource / Planning Model

- 状态：Accepted（direction 7）
- 关联：ADR-0182（Harness Resource Governor v1）、ADR-0136/0137（capability graph
  V7/V8）、#1472（governor-dispatch-gaps）、#1401（retry/replan fail-closed）、
  #1477（capability dispatch bind）
- 勘察/规格：`docs/dev/unified-cost-planning-recon.md`、`docs/dev/unified-cost-planning-spec.md`

## Context

Governor V1 建立了 rg.v1 数值估算契约与执行准入管线，但规划面与执行面的估算
互不感知：

1. planner（`candidate_planner_v8`）排序只看 latency 档位；`_COST_RANK` 定义后
   空挂；cost/resource 不是 plan candidate 的一等约束。
2. `estimate_for_node`（分类档位）与 `estimate_for_tool`（rg.v1 数值）是两套零
   互引用的口径 —— 同一工具在规划面与计费面可以是两个数。
3. 计划级聚合（`sum_estimates`）对所有维度朴素求和：wall_time 无 critical path、
   内存无并行 peak、无 retry 乘数 / cache 折扣 / optional-fallback 分支。
4. actual 用量只回填 wall_time_s；校准脚本只有 synthetic corpus；`measured` basis
   预留未实现。
5. replan/repair/deepen 循环只查次数预算（LOOP_BUDGETS），不消耗统一资源预算 ——
   计划层认为便宜、循环重算后实际爆炸的缺口仍在。

## Decision

1. **D1 单一估算真相**：governor rg.v1 `ResourceEstimate` 是唯一数值估算契约，
   先验表唯一驻留 `governor/estimation.py`。harness 侧新增
   `gis_harness/estimate_bridge.py`，`resource_estimate_for_node` 统一投影
   tool/model/algorithm 三面节点；`estimate_for_node` 改为「先桥后投影」——
   分类档位从 rg.v1 range 派生，basis 语义与既有签名保持。
2. **D2 计划聚合升级**：新增 `governor/plan_aggregation.py`（`aggregate_plan`）：
   wall_time 走 critical path（sequential sum / parallel max）、memory 走 live
   peak（sequential max / parallel sum）、累计维 sum × retry 乘数 × cache 折扣、
   OPTIONAL/FALLBACK 单列披露池、unknown 维保守地板参与。`sum_estimates` 保留
   兼容。不做第二套先验、不做第二套 ResourceEstimate。
3. **D3 resource-aware 排序**：`candidate_planner_v8` score 纳入 cost/memory 压力
   因子（rg.v1 投影，`GIS_RESOURCE_AWARE_RANK` kill-switch 回退 latency-only）；
   `capability_resolution._COST_RANK` 消费同一桥投影（修复空挂）。资源压力只改
   **eligible 候选间的排序**，绝不越过资格门槛；降级语义候选被选中时在 plan
   payload 显式披露 `DegradationSemantics`（不偷偷改变任务语义）。
4. **D4 actual 回填 + 有界校准**：dispatch adapter complete 时回填 result payload
   size 等廉价 actual；新增 `governor/calibration.py` `CalibrationStore`（bounded
   ring），`suggest_priors` 只产出**离线建议文件**，先验更新必须显式改表并评审
   —— 生产运行零自修改。
5. **D5 循环预算成本化**：`gis_harness/loop_budget.py` 把 LOOP_BUDGETS 次数闸与
   governor RetryBudget 令牌闸串联（replan→PI、repair→SELF_HEAL）；
   `plan_runtime.request_replan` 与 runtime_repair 记账点接入。governor 缺席时
   次数闸兜底（fail-open 到既有语义）。

## Consequences

- 正向：planner/解析/派发/循环共享同一估算口径；「计划认为便宜、执行爆炸」在
  计划层可见（retry 尾、并行 peak）；校准有了真实 actual 数据通路。
- 代价：`estimate_for_node` 内部多一次纯函数投影（μs 级）；排序行为变化由
  kill-switch 与确定性测试锁定。
- 风险控制：Governor 准入权不动；资格裁决不动；geocompute/modelops 账本不回灌。

## Out of scope（follow-up）

- workflow_runtime 节点级 ResourceEstimate 接入（本 PR 只做 harness 计划面与
  dispatch 面）；
- publication_export 串行锁与 governor EXPORT 通道双轨收敛；
- mapspec→RenderWorkInput 生产接线（`render_input_from_spec_summary` 投影本 PR
  就绪，消费点留给 cartography_runtime）；
- 前端 plan cost HUD。
