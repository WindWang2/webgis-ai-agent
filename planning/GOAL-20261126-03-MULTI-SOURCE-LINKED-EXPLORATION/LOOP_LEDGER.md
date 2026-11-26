# LOOP LEDGER — 03 多源空间联动探索

格式：`轮次 | 改动 | 验证 | 判定 | 下一步`

| 轮次 | 改动 | 验证 | 判定 | 下一步 |
|---|---|---|---|---|
| R1 | 仓库/PR/Pi 核对；建 worktree+分支 `zcode/goal-multi-source-linked-exploration-20261126`@`59eb3f527`；3 个只读探查子代理；planning 七件套 | G46 基线 77 passed(106s)；Pi journey 1 skipped(opt-in, 如实)；本域 Ruff 全绿；开放 PR=0 | 通过 | E1 证据整合 → WP1 失败用例先行 |
| R1 备注 | 证据要点：单源硬编码 S1–S10；扩展点 E1–E9；HTTP 路由层零测试（需先补网）；data_join_authoring 与 linked_exploration 互不引用（集成接缝=引用 G38 JoinPlan）；缺 weighted/rate/quantile 算子；无县级以下层级表（钻取诚实阻塞路径）；selection_history resolve_selection_ref 可做"刚才那组"指代 | — | — | — |

## 待办真相池（防丢）

- Pi journey 测试需 `PI_JOURNEY_E2E=1`（opt-in）；WP5 时决定是否本地跑（需 Pi 子进程构建）。
- openapi 快照 `tests/quality/snapshots/openapi.json` 需对账（新增路由后重新生成，走仓库脚本）。
- 前端测试命令待确认（frontend/ package.json scripts）。
- 主树 `zcode/full-code-review-fixes` 有未提交改动（其他线）——绝不触碰主工作树。

| R2 | WP1 契约 v3+关系(642632d04)；WP2 指标求值器(5b141a16f)；WP3 多源编排+导出(d10f74bb1) | 新增 71 单元+12 集成；全域 160 passed；Ruff 全绿；G46 基线 77 不退化 | 通过 | WP4 工具/REST/前端接线 |
| R2 备注 | 关键修复：evaluate_where 参数序；__other__ 键序列化；propagation_order v2 兼容；空传播集 handle 跳过；export 尾链抽共享（v2 回归曾破 4 项已复原） | — | — | — |
