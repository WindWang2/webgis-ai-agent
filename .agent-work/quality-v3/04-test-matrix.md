# 04-test-matrix — 最小本地验证矩阵（Epic §15 对照）

| Epic §15 必测项 | 实测位置 | 结果 |
|---|---|---|
| 两分支创建同 migration revision | test_quality_v3_merge_sim（同序号+fork）；完成证明脚本 | PASS |
| 两 ADR 号 | test_quality_v3_coordination::test_watermark_reds_on_new_duplicate | PASS |
| 同 tool/capability ID | merge_sim registry 轴（tool/algorithm/capability 三类） | PASS |
| 生成物无输入变更被改 | test_find_hand_edits_detects_content_change_without_input_change | PASS |
| stale 生成物 | test_ledger_is_current（V2）+ preflight generated_staleness | PASS |
| 公共 schema breaking | V2 api_compat 快照（新路由 additive +25 行验证） | PASS |
| WS/SSE breaking | V2 realtime 快照闸 + resume 套件（28 项） | PASS（未动契约） |
| migration up/down/up | test_sqlite_full_chain_up_down_up（34 revision 全链实测） | PASS |
| Postgres/SQLite 行为差异 | V2 test_storage_differential + PG opt-in lane | PASS（PG skip=opt-in 纪律） |
| Redis 丢失恢复 | chaos LOCK_*（进程内）+ real lane（opt-in 真实服务） | PASS |
| worker 丢失 | JOBS_WORKER_LOSS + harness kill -9 真实进程 chaos | PASS |
| 多进程 harness trace 关联 | integration_harness --chaos（traceparent 跨进程回显） | PASS |
| 前端重连 | tests/test_runtime_chaos_resume.py（28 项，存量覆盖）+ behavioral | PASS |
| flaky 归因 | runner --retry-failed（V2 --lf 机制保持） | 保留 |
| selector 漏报护栏 | test_real_branch_guard_v2_mapping_subset_of_selector（实景 diff） | PASS |
| merge sim 兼容重叠正例 | test_compatible_overlap_is_clean（线性链） | PASS |

## 本分支全量本地验证（精确结果）

- `pytest tests/quality/ tests/integration/ tests/test_alerts_metrics_consistency.py`：
  **459 passed, 7 skipped, 2 xpassed**（43.6s，两次连跑确认）
- quick lane（含 preflight + 前端行为闸）：integration profile 0.7s PASS
- impact profile：130.7s PASS（本分支全绿）
- frontend：tests/behavioral 7/7 绿；vitest 单文件实测
- integration_harness --chaos：0 failures
- 完成证明脚本：verdict PASS（exit 0）
