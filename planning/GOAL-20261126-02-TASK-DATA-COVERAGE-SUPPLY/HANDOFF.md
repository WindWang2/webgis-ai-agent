# HANDOFF — GOAL-20261126-02 交付摘要

## 最终状态（WP6 R2 前）

- 分支 `zcode/goal-task-data-coverage-supply-20261126`（基 master 59eb3f527）
- 核心增量：`app/services/task_data_supply/`（requirement/coverage/
  supply_plan/discovery/execution 五模块，mypy 清洁）+ 5 个 tier-2 工具
  + REST `/api/v1/task-data-supply` + 前端数据工作台「任务供给」页签
- 测试：99 passed（单进程）；面板 vitest 5；i18n/typecheck/eslint 与
  master 基线持平；ruff 全量与 master 逐字节一致
- 评审：R1 三方（A 科学/B 安全/C 旅程）→ 1×P0+8×P1+10×P2 修复 →
  R2 三方复审进行中

## 已知边界（如实声明）

1. 工具面 approved/choices 为调用方断言（与既有 materialize_dataset 同
   信任级）；签名封套式用户确认（user_decision_tools 模式）为后续线
2. 执行面 cancel_token 未接线（record_cancel 服务层可用；materialize
   取消走既有 durable 通道为后续线）
3. 执行面同步 ORM 在事件循环（与 #565 修复前的 data_fabric 同模式欠账）
4. OpenAPI 快照刷新含 master 既有漂移（+13434/-5339 干净 master 可复现）
5. 基线既有失败（与本线无关，失败集未扩大）：ruff 29、gis_harness 10、
   catalog audit 2、typecheck 5、i18n placeholder 1（chat.figureTopValues）、
   工具 conformance fail 11（他线遗留）
6. 无成本证据的候选 → budget_status=within 乐观缺省（P3-6 记录，后续披露）

## 恢复指引（上下文压缩/会话重开时）

1.读本文件 + LOOP_LEDGER.md + TEST_EVIDENCE.md + REVIEW.md →
2. `git log --oneline origin/master..HEAD` 核对提交链 →
3. `pytest tests/unit/task_data_supply/ -q --no-cov` 应 99+ passed →
4. R2 结论回填 REVIEW.md → PR
