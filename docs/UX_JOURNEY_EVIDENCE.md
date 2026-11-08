# UX_JOURNEY_EVIDENCE — 真实旅程证据（G05-SITUATED-WORKBENCH）

> 版本：1.0（2026-11-08）。任务书 J1–J8 旅程 → 证据映射。
> 诚实声明：本地验证以**真实产品链路测试**（真实 dispatch/ingest/投影/
  store）为主；无凭证浏览器真档（playwright real）未跑（需夜间账号），
  mock 档 e2e 基础设施与断言链在本 PR 变更面上回归。

## J1 成都学校分布 → 框选 20+ → 算这些对象的最近医院

| 环节 | 证据 |
|---|---|
| 框选 20 所学校 | 金标 `box_select_20_range_faithful`：投影 selection 20 id + matched_count=20 + ids_truncated=false + bbox（成都视口 104.04/30.65） |
| UI 反馈=Agent 事实 | `emitters.test.ts` SW-02：emit 携带全部 20 id + matched_count（修复前 .slice(0,8) 静默截断）；map-panel 传递 projection.matched_count（与 setBrushFeedback 同源） |
| "这些对象"指代 | `test_selection_range_disclosure.py::test_full_chain_20_ids_reach_claimed_scope`：ingest→投影→ClaimedScope.feature_ids 20 个 |
| 验证面 | `validate_analysis_scope` 五链（既有测试回归）+ 本 PR truncated_sample 披露 |
| 最近医院分析 | E5 多轮旅程（tests/acceptance/gis_agent/test_e5_multiturn_journeys.py，8 组真实 dispatch）回归绿 |

## J2 改色不重算

- map-op `set_layer_paint`/`set_layer_symbol` 走表现层 intent
  （PatchLayerStyle/UpsertLayer 延迟构建），不触发数据层分析：
  `tests/unit/gis_harness/map_operations/` 68 绿（含 SW-04 新 3 例
  ——paint-only 可逆/数值 before 语义）。
- 用户改色与 Agent 同 CAS 通道（user-mutation `patch_layer_style`）
  ——非第二真相。

## J3 切北京不复用成都层

- 会话切换隔离链（既有 + 本 PR 回归）：`session_switch_fresh_instance`
  金标；outbox 会话水印测试（`outbox.test.ts`）；回执总线跨会话丢弃
  （`receipt-bus.test.ts`）；`resetLiveState` 级联（session-cursor 测试）；
  跨会话 ref 拒绝（analysis_scope `ref_unresolvable`，五链测试）。
- adversarial SSE chaos 全量回归绿（本 PR 全域 3488 passed 内）。

## J4 A4 出版预览并说明限制

- 出版约束/版面求解属 #1975/#1993 已落地能力（非本方向增量）；
  A4 导出限制说明链未在本 PR 变更面上，回归由
  `docs/quality` 快照测试与 cartography lane 守护（lane 未触发）。
- 本 PR 相关面：终验收口行（FinalAcceptanceLine）对"出版渲染已观察/
  自动确认"如实披露（`map-harness-cards.test.tsx` 3 新）。

## J5 取消长操作 → 恢复

- turn epoch bump / abort 定向（#2009 已修，本 PR 回归）：
  `tests/test_adversarial_sse_chaos.py` + session-lock 重入测试绿。
- operation_id 幂等重放不重复图层/完成卡：map_operations 服务测试
  （replayed 粘滞回执）+ 回执卡按 operation_id 去重（receipt-bus FIFO）。

## J6 失效 ref → 修复卡

- `selection_ref_unresolvable` → 工具层最小澄清提示
  （`test_interaction_scope_tool.py` 回归绿）；layer_removed →
  claimed scope unusable + 情境一致性 `selected_feature_source_removed`
  不一致披露（situation consistency 测试）。
- VisualRepair/Clarification/EditProposal 卡既有（#1974/#1989），本 PR
  未改其行为。

## J7 多会话隔离

- 双标签页水印隔离金标 `two_tabs_reorder_visibility`；多实例 FIFO ≤8
  （contracts 测试）；J3 链全部适用。

## J8 移动端触控/键盘

- 键盘：图表类别原生 select（SW-08，`panel-ir-emit.test.tsx` 新例：
  select 存在/选项完整/选择同链/空值清除）；状态条 a11y
  （situation-status-bar 测试，role=status + aria-live）。
- 移动端底部 sheet/触控：#2008 已落地 Story 旅程面，本 PR 未变更；
  e2e 真档未跑（无凭证环境——诚实披露，见 TEST_MATRIX 未验证边界）。

## 状态复盘可复现性

```bash
# 后端全域（含旅程/竞态/金标）
.venv/bin/python -m pytest tests/test_gis_situation_*.py \
  tests/unit/gis_harness/ tests/unit/test_interaction_scope_tool.py \
  tests/acceptance/gis_agent/test_e5_multiturn_journeys.py \
  tests/test_adversarial_sse_chaos.py -q   # → 3488 passed, 4 skipped

# 前端全量
cd frontend && pnpm vitest run   # → 见 TEST_MATRIX（本 PR 车道全绿）

# 金标回放
.venv/bin/python -m pytest tests/unit/gis_harness/interaction_ir/test_replay_corpus.py -q  # 29 passed
```
