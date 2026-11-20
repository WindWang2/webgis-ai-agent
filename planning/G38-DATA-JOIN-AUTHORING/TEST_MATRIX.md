# G38 TEST_MATRIX — 验收场景 → 测试/证据映射

执行环境：worktree `g38-data-join-authoring-20261114T151651Z`；Python `.venv-g38`
（requirements.txt+dev 锁定安装）；前端 pnpm（vitest 4.1.12 / tsc / eslint）；
重阶段 flock `/home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock`。

## 后端单测（tests/unit/data_join_authoring/，61 passed）

| 场景 | 测试 | 结果 |
|---|---|---|
| J01 左关联保留+空值 | test_executor_attribute::test_j01_left_join_keeps_all_regions_with_null_population | PASS |
| J02 inner 对账 | test_j02_inner_join_reconciles_counts | PASS |
| J03 表×表无伪造几何 | test_j03_table_times_table_yields_typed_rows_no_geometry | PASS |
| J04 复合键防串 | test_j04_composite_key_prevents_cross_province_mixing | PASS |
| J05 类型/空值政策 | test_j05_type_and_null_policy_on_executor + test_j05_explicit_transform_applied + keys 6 项 | PASS |
| J06 全量重复键阻断 | test_j06_full_scan_blocks_many_to_one_with_duplicate_right（>50 行夹具） | PASS |
| J07 一对多展开+lineage+唯一身份 | test_j07_one_to_many_expansion_lineage_and_unique_ids | PASS |
| J08 乘积分配前拦截 | test_j08_pair_budget_estimated_before_allocation | PASS |
| J09 同名/保留字段冲突 | test_j09_same_name_and_reserved_field_conflicts | PASS |
| J10 多命中三策略 | test_executor_spatial::test_j10_*（require_unique 阻断/expand_all/pick 确定性+并列拒绝/aggregate） | PASS |
| J11 边界/洞 oracle | test_j11_within_excludes_boundary_covers_includes + test_j11_hole_point_not_inside（P7 边界点 oracle） | PASS |
| J12 异 CRS 变换 | test_j12_crs_mismatch_transforms_points_to_polygon_frame（4326 点→3857 面，working_crs 证据） | PASS |
| J13 未声明/偏移 CRS | test_j13_undeclared_crs_blocked + test_j13_offset_frame_blocked | PASS |
| J14 空/全未匹配契约 | test_j14_empty_and_all_unmatched_contracts | PASS |
| J17 源漂移 typed stale | test_service_flows::test_j17_source_drift_after_submit_blocks_stale_execute | PASS |
| J19 幂等收敛 | test_j19_idempotent_reexecute_converges_same_run | PASS |
| J21 恢复收敛 | test_j21_recovery_converges_published_content_without_run_row（含反例：无收据→failed） | PASS |
| J22 越权 | test_j22_cross_session_denied + 路由 404 隔离 | PASS |
| J26 运行时预算 | test_perf_and_cancel::test_j26_runtime_pair_cap_stops_early（<10s 即停） | PASS |
| J27 聚合 null | test_j27_aggregate_null_semantics（全 null 组 sum/mean=None；valid_count 分母） | PASS |
| J28 内容失效 | test_j28_deleted_content_invalidates_reuse（inspect typed 失效 + 授权新执行恢复） | PASS |
| J20 取消 | test_perf_and_cancel::test_j20_cancel_converges_no_public_artifact（DB 取消→看门狗→检查点→cancelled，无终态包） | PASS |
| 差分兼容 | test_attribute_matches_federation_kernel_semantics（federation attribute_join_local 对拍：null 排除/左序/右输入序逐对相等） | PASS |
| J18 重放一致 | test_executor_spatial::test_deterministic_replay_same_snapshot_identical_output | PASS |
| 计划/CRUD/CAS/词表 | test_keys_and_plan 15 项 + test_service_flows CRUD/preview 证据 | PASS |
| 工具/路由面 | test_tools_and_routes 7 项（注册钉死、_TOOL_MODULES 索引、standalone app 全生命周期、CAS 409、typed 400/404、终态取消幂等） | PASS |
| 性能曲线 | test_attribute_perf_curve[1000/5000/20000]（高匹配率；20k <60s 实测 ~1-3s 量级） | PASS |

## Pi 真实旅程（PI_JOURNEY_E2E=1，1 passed）

`tests/integration/test_pi_g38_data_join_journey.py`：真实 vendor Pi（pin b4afdddf
构建）+ node stub LLM + 生产 uvicorn。15 门全过：
warmup / describe×2（row_count=1101）/ plan_upsert（revision=1）/ DB 计划行 /
preview 全量计数（matched_pairs=1100、双侧未匹配 1/1、basis=full）/ run
completed（DB）/ 收据计数（output_rows=1101）/ result 分页 total_rows=1101 /
输出文件 1101 行 / 抽值 oracle（pop=10000、未匹配区 pop=None+unmatched）/
CRS 保留 / 身份保留（_wfid=wf1-b0）/ 渲染 ref 1101 / G18 delivery completed /
下载回读 1101 行与值一致。**覆盖 J15（1000+ 匹配行经 Pi，成果完整非 200 行
截断）与 J25（Pi→dispatch→服务→成果→G18 导出回读）。**

## 前端（vitest --no-file-parallelism，4 passed；tsc 主配置干净；eslint 0 警告）

- 双源检查卡（行数/CRS/几何披露）
- blocked 诊断如实展示（cardinality_violation + 修正提示；不显绿灯）
- 执行→completed：全量行数 + 分页 + 加入地图（hud 层绑定服务端权威渲染 ref）
- 纯表输出禁用加入地图（诚实披露）
- J24 语义（图层从权威输出读取）经渲染 ref + addLayer(_refId) 链路覆盖；G33 接续由图层面板既有能力承接（专题面板消费上图图层）

## 定向回归

| 面 | 结果 |
|---|---|
| tests/unit/test_data_fabric_federation.py + test_geocompute_execution.py + tests/jobs + tests/data_operations | 328 passed, 1 failed（geocompute reuse —— 干净 base 同败，既有） |
| tests/test_alembic_metadata.py + tests/test_deploy_migration_wiring.py | 27 passed + 2 failed（percent-encoded URL —— base 同败既有；alloc/单头/CHECK 词表/迁移链 ✅；`alembic upgrade head` 干净库实测 0107 ✅） |
| tests/quality/ + tests/test_api_auth_required.py | 544 passed, 3 failed（棘轮基线漂移/science 切片/auth 白名单 —— base 1276692737 同败，既有；派生工件已按流程再生成） |
| G18 交付 tests/delivery | 随 quality 扫描通过（delivery 面在 tests/quality + 集成内覆盖；schema_info.crs 改动仅加性） |

## master 既有失败清单（干净 base 复现证据，非本分支引入）

1. tests/test_alembic_metadata.py::test_alembic_upgrade_head_survives_percent_encoded_url
2. tests/unit/test_geocompute_execution.py::TestExecutor::test_reuse_and_invalidation
3. tests/quality/test_findings_ratchet_gate.py（基线 vs G34/G35 描述符漂移）
4. tests/quality/test_quality_scenario_corpus.py::test_slice_geometry_states_green[science_]
5. tests/test_api_auth_required.py::TestAllMutatingRoutesAuth（data_import/pi_interactions/temporal_media 白名单）
