# REVIEW — GOAL-20261126-01

## 独立 review（未写被审文件的子代理，2026-11-27）

范围：`git diff 59eb3f527..HEAD` 全量（4 commits）；验证方式 = 逐行审读 +
约 30 个对抗探针 + 复跑关键套件（69 passed）+ Ruff。

**结论：无 P0/P1；2×P2 + 5×P3。**

| # | 级别 | 摘要 | 处置 |
|---|---|---|---|
| F1 | P2 | 任务闸平级语义与 decide_task 的 span 裁决序矛盾；docstring 与实现不一致；高特异性选择未覆盖 | ✅ 已修：`(spec,span)` 全序平级闸 + 高特异性拒绝测试 + 文档对齐（2ad114767） |
| F2 | P2 | dedupe 并发竞态（check-then-set 跨 await）+ request_id 跨会话全局耦合 | ✅ 已修：in-flight 占位（并发同 id 单次传输）+ scope 参数（session 隔离），wiring/工具透传 sid |
| F3 | P3 | 自报 confidence 未与 probabilities 分布交叉校验；usage totalTokens 接受 bool | ✅ 已修：min(confidence, P(所选)) 保守校验 + bool 拒绝 |
| F4 | P3 | query_local_poi「输入面等价」非字面成立：4 种旧接受但无意义形状现校验期响错 | ✅ 已修：docstring 如实披露（合法输入面不变） |
| F5 | P3 | 测试卫生：事件循环泄漏、importlib 路径耦合、TIE_QUERY 注释未披露 span | ✅ 已修：asyncio.run、路径守卫、注释修正 |
| F6 | P3 | SCOPE 禁触 `app/tools/**` 被两处必要修改触碰未回写 | ✅ 已修：SCOPE 所有权校正记录（map_plan_tools 接线 / local_stats args_model） |
| F7 | P3 | 单例生命周期（首用冻结 env/换 transport 丢 dedupe 状态） | 📝 记录为已知取舍（reset/注册是显式管理点，风险 benign） |

安全/授权域结论（reviewer 原文）：决策结论无法扩大数据访问、批准 destructive、
绕过 merge_intent_hints 护栏；clarification 变更 value-safe；decision_meta
有界且不进错误路径。对抗探针（同形字/零宽字符/NaN/越界/多余 answers/畸形
形状/边界值）全部 typed 拒绝，选择永不逃逸候选闭集。

## 复审（主 Agent 对修复的核验）

- 修复后全量决策面 74/74 passed（含新增：全平级/span 分胜负/高特异性
  gate、并发幂等、scope 隔离、分布交叉、usage bool）+ Ruff 全绿。
- map_plan 回归 6 passed（scope 透传后）。
- schema 卫生车道 14 passed + 1 xfailed（reason 清单与实现一致）。

## 预存失败（base 对照实证，非本线回归，只记录不扩线）

1. `tests/integration/test_pi_stub_llm_journeys.py::test_a5_journeys_on_real_pi_bridge`
   —— 本分支与干净 base `59eb3f527`（临时 detach worktree + 同一 vendor dist）
   同样失败于 J6b（turn-2 selector 未激活 nearest_facility → step_error）。
   命令：`PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_stub_llm_journeys.py -q`。
2. `tests/test_tool_meta_contract.py::test_extension_example_names_exist` /
   `test_extension_routes_distribution_before_status_pull` —— base 同样失败
   （index.mjs 的 `e.g.,` 示例清单正则失配，系先前已合并线引入的漂移）。
