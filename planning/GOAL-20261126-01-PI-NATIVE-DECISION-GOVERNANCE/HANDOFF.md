# HANDOFF — GOAL-20261126-01

## 交付摘要（2026-11-27）

- 分支 `zcode/goal-pi-native-decision-governance-20261126-172144`
  （base `59eb3f527` = origin/master @ #2282，推送时未移动）；
  worktree `webgis-pi-native-decision-governance-20261126T172144Z` 保留。
- vendor/pi pin：v1.6.1（`ef4a1cfc`）不变；上游执行时实查仍为最新 stable。
- 6 commits：WP2 核心 / WP2 接线 / WP3 schema 修复 / 矩阵 §6 / review 修复 /
  账本终稿。16 files，+1906/-9。

## 用户旅程与真实调用路径

1. **任务识别决策辅助**（生产入口 `webgis_compile_map_plan` 工具，被 Pi
   agent 经 ToolDispatch 真实调用）：`PI_DECISION_ASSIST=1` 且配置
   classifier 时，候选 ≥2 的查询出题 → 传输 → 全序平级闸 →
   merge_intent_hints（protected 护栏）应用；关闭/无 provider/拒绝 →
   确定性结果不变。receipt 披露 `decision_assist` meta。
2. **schema 卫生**（生产入口 = 每次 spawn 的 dump + dispatch 输入闸）：
   `query_local_poi` 全节点 typed（bare 清零），输入面等价矩阵锁定。
3. **阻塞/取消/澄清负例**：沿用既有三代协议测试（未重复造轮子），
   审计结论入矩阵 §6。

## 已知限制与未完成（如实）

- classify 生产传输未接线（工具 dispatch 期间扩展被占用，poll 循环
  复杂度高——裁定记录于矩阵 §6 与 WP1 台账）；真实 provider 无凭据未实测。
- 模板候选域未接线（tie 探测点在 recipe 选择后）；clarification bool
  域以默认项翻转机制覆盖（单测级）。
- 预存失败（base 对照实证，非本线）：stub journey J6b、meta_contract 2 项
  （详见 REVIEW.md）。

## 回滚

- 全部改动为增量（新模块 + 工具内 feature-gated 调用 + args_model）；
  revert 分支即可；无迁移、无 schema/DB 变更、无 pin 变更。
- 运行时关闭：不设 `PI_DECISION_ASSIST`（默认关）即恢复原行为。
