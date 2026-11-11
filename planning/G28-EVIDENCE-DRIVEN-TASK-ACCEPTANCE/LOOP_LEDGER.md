# G28 LOOP_LEDGER

## 里程碑

| # | 时间(UTC) | HEAD | 内容 | 状态 |
|---|---|---|---|---|
| M1 | 2026-11-12 | 5592011ff | W1+W2 证据面加性扩展（receipts/semantics/evaluator/requirements/projection）+ 语料 csv oracle 更新；goal_satisfaction 套件 38 绿 | verified |
| M2 | 2026-11-12 | ce77a0628 | W3 finalizer 接线（锁外预取 receipts+frame 切面、块持久化、评估快照）；wiring 15 例 + 既有回归 97 绿 | verified |
| M3 | 2026-11-12 | 12c7d5597 | W4 前端完成卡（parser/reducer/card/chat-tab/SSE/i18n）+ W5 repair_actions + goal_repair_plan 块；后端 9 例 + 前端 10 例 + tsc 绿 | verified |
| M4 | 2026-11-12 | 846ff5d11 | W6 语料 receipt 族 118 案例（12 假成功负例 + 2 合法无地图 + P4 旗舰叙事）；corpus 测试 4 绿 | verified |
| M5 | 2026-11-12 | (working) | W7 旅程驱动加 G28 断言（SSE 与落库块同 verdict）；vendor 构建待做 | in_progress |

## 终态（2026-11-12 UTC）

- **PR**: https://github.com/WindWang2/webgis-ai-agent/pull/2090 （已创建，**未合并**）
- 分支/HEAD：`zcode/g28-evidence-driven-task-acceptance-20261111T153846Z` @ `343b09613`
- BASE：`b8f2a6ecc`（推送时 origin/master 未动，无需整合）
- 提交链（7）：5592011ff → ce77a0628 → 12c7d5597 → 846ff5d11 → 1b1af5c44 → 787cc62ec → 343b09613
- 独立评审：round1 APPROVED（1 P1 + 11 P2）→ fix 787cc62ec → round2 APPROVED
  （伪造 harness 复跑全 HOLD + 零漂移数值验证）→ 残留 P2 加固 343b09613 → 复验 partial
- 全量套件 triage：51 失败/错误与干净 BASE 逐项一致（零引入）
- 子代理用量：3 个名额中用 2（只读调查员 1 + 独立 reviewer 1；实现协作者未启用 —— 主 Agent 直接实现）
- 后台进程：无遗留（next dev/uvicorn/node stub 均已退出；宿主锁 exec 模式自动释放）

## 有效契约（必须保留）

- 单一 evaluator（A02）；visual/assisted 永不单独背书；PASS_CAPABLE_CLASSES 白名单不变。
- 证据面加性：`evaluate_goal_satisfaction(..., receipts=None)` 默认 None = 旧调用零漂移。
- frame_product/capability_receipts/goal_repair_plan 均为 additive 块键（缺席省略）。
- csv 归数据交付族（A16）—— 地图导出回执不背书交付需求（oracle 已更新，理由见语料注释）。
- 生产块 render_status 恒带词（默认 unknown）；空串 = 旧合成形状（A25 兼容，evaluator 不收紧空串路径）。
- 交付回执修订空间 ≠ MapSpec 修订空间：delivery 证据不做交叉 stale 比较（身份靠 sha256/missing_ids）。
- kill switch `GIS_GOAL_RECEIPT_ADAPTERS=0` 关闭全部回执收集（证据面回旧形状）。

## 未解阻断 / 已知问题

- vendor/pi 子模块在本 worktree 未初始化（`git submodule status` = `-`）→ A26 旅程需先构建。
- 全量 gis_harness 套件存在 28F/23E 失败（BASE 对照进行中）；`test_pr1972_integrity::test_live_raster_identity_detects_unsampled_middle_write` 已确认 BASE 复现（pre-existing）。
- 浏览器 E2E（level-3）：Playwright 基建待查；若 blocked 如实记录。
- refresh 族：session→project 桥不存在 → 仅 chapter/project_id 显式在场时消费（A30 诚实缺席）。
