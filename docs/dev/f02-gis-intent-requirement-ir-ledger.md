# F02 — GIS Intent & Requirement IR · Ledger（执行账本）

- 分支：`zcode/f02-gis-intent-requirement-ir-20260926-9e1ad229`
- worktree：`../wt-webgis-f02-gis-intent-requirement-ir-20260926-9e1ad229`
- 基线：origin/master `9e1ad229`（2026-09-24）
- ADR：`docs/adr/0215-gis-intent-requirement-ir.md`

## 里程碑

1. `72288938` docs：recon（五列表 + open-PR overlap 检查）+ decisions（D-01..D-12）+ ADR-0215。
2. `9e5b4bcf` feat：requirement_ir 包（11 模块）+ 8 场景 corpus + tools 加法接线 + 169 tests。
3. review-fix：Subagent C 独立深审（APPROVE-WITH-FIXES）→ 3×P1 + 10×P2 修复 + 8 条回归测试（`docs/dev/f02-gis-intent-requirement-ir-review.md`）。

## DoD 对账

- [x] query-only / analysis / map / edit / export 五类意图覆盖且互不误判（`test_classify.py` 正例+负例矩阵、corpus 验收）
- [x] clarification 有明确 reason code；同一 ambiguity 在 context 未变化时不重复询问（`ClarificationCode` 策略表 + context_key 去重 + `test_clarify.py`）
- [x] Requirement patch 可 replay（折叠后仍成立）、可 diff、可归因（`test_patch.py` + `test_review_fixes.py::TestJournalFoldingReplay`）
- [x] MapSpec/工具选择仍由既有下游权威产生：六路投影只构造 GrammarRequest/FieldQuery/GoalRequirement 等输入面并以真实下游类型构造期校验；IR 无任何工具调用/MapSpec 写入路径

## 本地测试证据（pytest -n 0 串行）

- `tests/unit/gis_harness/requirement_ir/`：176 passed + 1 skipped（条件 corpus 路径）
- 邻域回归：test_intent / test_action_intent / test_goal_satisfaction_v1 / test_intent_diff / test_goal_graph / test_tool_meta_contract / test_intent_adaptive / test_intent_replan_envelope / test_goal_satisfaction_wiring / test_intent_learning：171 passed
- ruff（新包 + tests + tools.py）：clean

## 资源纪律

全程 `pytest -n 0` 串行、无全量 xdist、无构建并发；最大并发重型进程 = 1。

## 剩余风险 / 后续项

见 review 文档 P2 后续清单（representation 面下游接线、HMAC 化 document_id、归因匹配精化、并发 gather 测试）。
