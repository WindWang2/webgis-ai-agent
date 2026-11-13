# G32 TEST_MATRIX — 验收场景 → 测试映射与执行证据

## 单元/集成层(tests/unit/feature_proposals/,33 项;pytest 单进程)

| 场景 | 测试 | 文件 | 结果 |
|---|---|---|---|
| P01 保存→关闭→重启恢复,当前 ref 未改 | test_p01_create_reload_restore(新 store 实例读同一文件;diff 重算) | test_service_journey.py | PASS |
| P02 current 改另一要素 → 独立保留双方 | test_p02_independent_change_preserved(rebase 后 merge 双方保留) | 同上 | PASS |
| P03 同要素互不依赖字段 → clean | test_p03_same_feature_independent_fields | test_conflict_matrix.py | PASS |
| P04 同字段不同值 → 冲突+批准拒绝 | test_p04_same_field_conflict_blocks_merge | test_service_journey.py | PASS |
| P05 同字段同 typed 值 → satisfied | test_p05_same_field_same_value_satisfied | test_conflict_matrix.py | PASS |
| P05b 0 vs "0" 不吞并 | test_p05b_zero_vs_string_zero_is_conflict | 同上 | PASS |
| P06 null/missing/False/0 严格 | test_values_equal_strict_matrix + test_p06_missing_vs_null_conflict + test_p06_false_vs_zero_conflict | 同上 | PASS |
| P07 双改几何不平均 | test_p07_geometry_both_modified(digest 并存) | 同上 | PASS |
| P08 属性 vs 几何独立 | test_p08_attribute_vs_geometry_independent | 同上 | PASS |
| P10 提案改/current 删 | test_p10_update_on_deleted_target | 同上 | PASS |
| P11 提案删/current 改 | test_p11_delete_vs_update | 同上 | PASS |
| P12 child wfid 稳定/换 seed 变 | test_p12_split_child_identity_stable_across_reloads | 同上 | PASS |
| P13 身份可证性 | conflicts.identity(provable 字段;P01 未物化 base 快照旅程覆盖) | 同上 | PASS |
| P14 重复自然键 → id_ambiguous(不按位置迁移) | test_p14_duplicate_natural_key_blocks + _align_unmaterialized_current | 同上 | PASS |
| P15 多要素异构单 plan | test_p15_multi_feature_heterogeneous_deltas(手写 oracle) | 同上 | PASS |
| P16 批准后内容变更失效 | test_p16_approval_invalidated_by_content_change | test_recovery_and_auth.py | PASS |
| P17 revision CAS(单实例+双实例) | test_p17_revision_cas_concurrent_update + test_p17b_two_store_instances_cas_and_restart | 同上 | PASS |
| P18 提交窗口崩溃恢复 | test_p18_recover_executed_after_window_crash(journal 证据→同请求恢复 merged,单次写入) | 同上 | PASS |
| P19 recovery_required+盲重试阻断 | test_p19_unprovable_blocks_blind_retry(merge 拒绝+resolve unprovable 拒绝) | 同上 | PASS |
| P20 长期 merged 重放 | test_p20_merged_replay_from_persistent_receipt(journal 逐出后) | 同上 | PASS |
| P22 跨会话/未授权 | test_p22_cross_session_ref_rejected + test_p22_proposal_scoped_to_session | 同上 | PASS |
| P23 工具无批准面 | test_p23_agent_tools_have_no_decision_entry(结构性) | 同上 | PASS |
| P27 withdraw 共享 blob | test_p27_withdraw_releases_shared_snapshot_safely | 同上 | PASS |
| P28 主链(approve→merge→数据→幂等→undo) | test_p28_approve_merge_updates_real_data | test_service_journey.py | PASS |
| G14 适配专项 | plan_seed/op 级 wfids(异构/向后兼容/结构性拒绝) | test_g14_adapter_and_tools.py | PASS |
| 工具真实 dispatch | test_tool_create_and_dispatch_roundtrip | 同上 | PASS |

## 前端(vitest,4 项)

队列→详情→裁决旅程 / clean 批准→merge / 冲突时不出现批准 / recovery 披露 — feature-proposal-drawer.test.tsx 全 PASS;eslint 0 警告;tsc 通过。

## Pi 全链(opt-in PI_JOURNEY_E2E=1 + heavy 锁;tests/integration/test_pi_g32_feature_proposal_journey.py,4 项 PASS,~16-180s)

真实 vendor Pi 子进程(pin b4afdddf,v1.5.2 已构建)+ 生产 webgis-tools 扩展 + 真实 ToolDispatchService + 生产 app(uvicorn):
- J1 tool_search(中文 BM25 命中)→ vector_change_proposal_create 真实 dispatch → 持久 proposal(submitted);
- J2 REST 可见(author=agent);
- J3 owner REST 批准+merge(origin=feature_proposal);
- J4 真实数据逐要素核对(777/888 应用;sch-2/sch-3 未触及)。
验证层级记录:Pi 子进程 ✅ + 后端服务 ✅ 同链;浏览器层由 vitest 组件旅程覆盖(mock API;真实浏览器 E2E 未跑,如实披露)。

## G14 回归(既有)

tests/unit/feature_edit/ 全量 123 项 PASS(适配无回归)。

## 未测/披露

- 真实浏览器(Playwright)端到端未执行(本任务以组件级真实交互 + REST/Pi 全链为证);MapSpec stamp 延迟收敛(P21)依赖 G14 既有机制(stamp_status 披露),未单独注入测试。
- 大规模性能(近 20k 要素提案构建/rebase 耗时)未做基准 — 上限闸(≤200 目标/≤8 ops/快照 ≤8MB)已由 typed 拒绝路径覆盖。
