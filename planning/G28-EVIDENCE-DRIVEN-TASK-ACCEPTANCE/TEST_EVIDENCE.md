# G28 TEST_EVIDENCE（BASE=b8f2a6ecc → HEAD 更新中）

## 命令与结果（本 worktree 实跑）

| 日期(UTC) | 命令 | 结果 |
|---|---|---|
| 11-12 | `pytest tests/unit/gis_harness/test_goal_satisfaction_{v1,wiring,corpus}.py --no-cov -q` | 38 passed（含 112→118 案例语料） |
| 11-12 | `pytest tests/unit/gis_harness/test_goal_satisfaction_finalizer_wiring.py --no-cov -q` | 15 passed（1 处 facet not_bound 修正后） |
| 11-12 | `pytest tests/unit/gis_harness/test_goal_satisfaction_repair_actions.py ...wiring... --no-cov -q` | 31→32 passed（MAX_BLOCKERS 4→6 修正） |
| 11-12 | 全 goal_satisfaction 套件（v1+wiring+corpus+finalizer+repair） | 62 passed |
| 11-12 | `pytest tests/unit/gis_harness/test_{runtime_state_machine_v7,claim_ingest_session_1391,map_product_unified_completeness,map_completion}.py --no-cov -q` | 97 passed（finalizer 邻接回归） |
| 11-12 | `pytest tests/unit/gis_harness --no-cov -q`（全目录） | 3464 passed, 28 failed, 23 errors, 1 skipped + 1 deselected |
| 11-12 | 干净 BASE 复现（临时 worktree @b8f2a6ecc，同文件集） | 失败名单**逐项一致**（51 项，`comm` 零差异）→ 全部 pre-existing，G28 零引入 |
| 11-12 | frontend `vitest run --project functional lib/types/goal-satisfaction.test.ts components/chat/goal-satisfaction-card.test.tsx` | 10 passed（vitest 4.1.12，local node_modules） |
| 11-12 | frontend `tsc --noEmit`（React 19 JSX namespace 修正后） | exit 0 |
| 11-12 | 旅程驱动 + pytest 侧 G28 断言（SSE goal 投影 ≡ 落库块 verdict） | 待跑（vendor 构建中） |

## 失败 triage（全目录 28F+23E）

文件分布：capability_graph_v8(23E) / visual_blob_lifecycle(9F) / visual_regression_corpus(5F) /
workflow_guards(3F) / visual_staleness_gate(2F) / visual_repair_endpoint(2F) /
component_composition(2F) / binding_taxonomy(2F) / semantic_v2_families(1F) /
review_harness_medium(1F) / r1_review_fixes(1F)。
**BASE 复现结论**：`/tmp/base_failures_full.txt` vs `/tmp/head_failures_full.txt` comm 双向空 →
全部为 master 既有失败（视觉族 + capability graph fixture + workflow guards），与本分支无关。
另：`analysis_workflows/test_pr1972_integrity::test_live_raster_identity_detects_unsampled_middle_write`
同样 BASE 复现失败（两侧记录）。

## 验收矩阵证据映射（当前）

- A01 adapter 生产调用：finalizer wiring 测试 + pipeline 持久化断言 ✅
- A02 evaluator 唯一：goal_satisfaction 套件 62 绿（无第二评估入口）✅
- A03 success≠显示：GC-receipt-computed-but-not-displayed + render unknown 闸 ✅
- A05 旧 revision：GC-receipt-frame-stale-observation-partial + export stale（既有）✅
- A06/A18 身份：GC-receipt-delivery-artifact-mismatch + wiring revision 对账 ✅
- A07 partial 全集：GC-receipt-compute-partial-population + scope_subset ✅
- A08/A09 计数率/单位/CRS：GC-receipt-compute-density-undeclared + caps_from_compute ✅
- A10/A11 空/缺分母：GC-receipt-import-empty-legal（合法空≠失败）+ density cap ✅
- A12 user 锁优先：repair_actions blocker 测试（user_owned → 无动作）✅
- A13/A14 frame：GC-receipt-frame-snapshot-only-partial + frame 闸 wiring ✅
- A15/A16 类型不混：GC-receipt-no-map-statistics-legal + GC-receipt-delivery-current-fulfilled +
  GC-export-csv-map-export-receipt-rejected ✅
- A19 取消≠complete：GC-receipt-delivery-cancelled-absent ✅
- A20 快照裁决：test_late_receipt_does_not_change_persisted_verdict ✅
- A21/A22/A23 repair 预算/无进展/不盲重写：repair_actions 套件（blocker 码 + expected_new_evidence + 路径注册）✅
- A24 三面同裁决：bounded_payload.requirements + 前端 parser/card 测试 + 旅程断言（待 A26 实跑）⏳
- A25 旧回执 unknown 中性：GC-receipt-legacy-fields-unknown-neutral ✅
- A26 真实 Pi 闭环：待旅程实跑 ⏳
- A27 观察缺席不伪造：frame not_observed 默认 + render unknown 语料 ✅
- A28 ≥6 端到端独立 oracle：语料 118 案例（12 假成功 + 2 合法无地图 + 6 任务族）✅
- A29 数据更新失效旧证据：test_late_receipt + GC-receipt-refresh-stale-observation-partial + finalizer 漂移守卫（既有）✅
- A30 独立运行：仅消费已合入接口；G22-27 缺席 → unknown/not_observed（wiring 测试断言 refresh 空/frames not_observed）✅

## 假成功负例清单（reviewer 重点，共 12，全部 must_not_pass）

见语料 `_family_receipts` + `GC-export-csv-map-export-receipt-rejected`：
scope_subset / artifact_mismatch / cancelled / partial_population / density_undeclared /
import_failed-vs-row / success-string-no-identity / snapshot-not-live / stale-frame-observation /
refresh-stale / wrong-receipt-path / computed-not-displayed。

## 资源记录

- 并行纪律：pytest 单进程（无 xdist）；vitest `--project functional`（单 fork 语义由 config 决定）；pnpm install --prefer-offline；重任务（vendor build / 全量 pytest）持宿主锁 `heavy`（`.host-lock/hostlock.py`，msvcrt 字节锁）。
- node_modules/pnpm：worktree 内独立安装（frozen-lockfile 因 master 自带不匹配改 no-frozen；**lockfile 已还原未提交**）。
