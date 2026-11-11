# TEST_MATRIX · G13-CAPABILITY-EXECUTION

工作树：`webgis-worktrees/goal-g13-capability-execution-20261110T133500Z`
Python：主仓 `.venv`（3.13.17）；pytest workers=1（资源纪律）。
所有命令真实执行于本工作树，非主仓。

## C 场景矩阵 → 证据

| 场景 | 测试 | 结果 |
|---|---|---|
| C01 Pi 请求 p90（选择+产物） | `test_g13::test_wp04_real_pi_turn_marker_changes`（真实 turn 名单）+ `test_g13::test_c01_real_dispatch_mints_real_artifact_ref`（真实 dispatch；p90 与 numpy 手算对拍 ≤0.5；有效样本分母在 science/data） | PASS |
| C02 全 nodata 分区 | G06 既有 `test_cap_o08_*`（nodata 掩码/有效样本计数）+ seam 后 `n_samples=0 → quantiles=None`（实现保留 G06 语义；`valid_count=0` 分支不产 NaN） | PASS（实现既有） |
| C03 第二轮读上一轮结果 | `test_g13::test_c03_next_turn_reads_previous_artifact_via_ref`（zones 传 `ref:geojson-*` → registry 生产解引用 → zone_count 一致）+ `test_c01`（data_ref 存储读回 = 首轮数据逐位一致） | PASS |
| C04 真实边时间 | G06 既有 `test_cap_o09_*` + `test_r2_n02_feature_time_is_apportioned_across_segments`（多段分摊不重复计总时间） | PASS（既有） |
| C05 只有几何长度 | G06 `test_cap_o06_network_seconds_distance_returns_typed_error`（tool 层）+ `test_g13::test_selector_time_context_suppresses_metric_tools`（检索层 UNIT_DIMENSION_CONFLICT） | PASS |
| C06 高相似停用工具 | `test_g13::test_c06_deactivated_high_similarity_tool_excluded` + kill-switch 反向（关闭后回到名单=证明经 selector） | PASS |
| C07 选后撤权/停用 | `test_g13::test_c07_revoked_after_selection_recheck_refuses_and_releases`（拒绝+释放占位+替代；复核关闭后同输入真实执行=故障反验） | PASS |
| C08 同名 schema 换代 | `test_g13::test_c08_same_name_schema_drift_refused`（STALE_TOOL_SCHEMA） | PASS |
| C09 租户隔离 | `test_g13::test_wp09_invalidation_does_not_break_other_tools`（同快照对象：空权限 forbidden vs 无上下文全量 —— 结构缓存共享、权限请求侧） | PASS |
| C10 科学画像缺失 | G06 oracles（`not_evaluated` 披露不冒充 eligible）+ `test_g13::test_wp06_primitives_resolvable_from_capability`（三态） | PASS |
| C11 合法 geodesic 不误杀 | G06 `test_cap_o06_geographic_linear_distance_ineligible`（仅 linear-required 才拒；geodesic 不受投影检查影响） | PASS（既有） |
| C12 分位数缺、均值在 | `test_g13::test_c12_quantile_unavailable_mean_not_silent_substitute`（均值保留但披露降权，不宣称等价） | PASS |
| C13 10 并发冷请求 | `test_g13::test_c13_same_generation_single_flight_and_zero_hot_compile`（compiler 调用=1；热命中=0 增量） | PASS |
| C14 大候选/低预算 | 既有 `test_pi_surface_budget.py`（前门保留/裁剪记因）+ `test_g13::test_unverified_tools_kept_visible_and_core_frontdoor_never_suppressed` | PASS |
| C15 拒绝/异常后重试 | `test_g13::test_c15_denial_then_recovery_no_stuck_placeholder`（恢复后同参真实执行，无 Loop blocked） | PASS |
| C16 恢复会话/假 ref | `test_g13::test_c16_fake_and_cross_session_refs_rejected`（伪造/跨 session typed 拒绝）+ C03（本会话产物可读） | PASS |

## 执行过的命令（真实结果）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/gis/test_g13_capability_execution.py` | **26 passed** |
| `pytest tests/unit/gis/test_capability_ecosystem_oracles.py` | 19 passed |
| `pytest tests/unit/tools/test_capability_ecosystem_primitives.py` | 9 passed |
| `pytest tests/unit/gis_tool_conformance/` | 全 passed（含 ratchet：master 上 66>64 已红，本线回到 64=通过） |
| `pytest tests/unit/gis/test_g02_science_oracles.py` | 22 passed（计数断言 253→255 后） |
| `pytest tests/unit/test_tool_retrieval_v4.py` | 23 passed |
| `pytest tests/unit/test_pi_dynamic_surface.py test_pi_native_surface.py` + Pi surface 套件（budget/metrics/parity/abi/h01） | 全 passed（48+25+…） |
| `pytest tests/unit/test_capability_dispatch_*` + `test_audit_1395_decision_chain.py` | 全 passed |
| `pytest tests/unit/test_llm_result_formatter.py test_artifact_registry.py test_parallel_tool_dispatch.py` | 39 passed |
| `pytest tests/unit/test_pi_post_dispatch_pipeline.py test_pi_dispatch_adapters.py test_turn_settlement_seam.py` | 42 passed |
| `python scripts/verify_capability_ecosystem.py` | **6/6 passed** |
| `python -m app.lib.gis.execution_catalog_docs --check` | up to date |
| `python scripts/gen_science_catalog.py` | 重生成（255 算法） |
| `ruff check app/ tests/unit/gis/test_g13_…` | All checks passed |
| `mypy`（3 个新模块） | Success: no issues |

## 性能证据（§七：工作量而非绝对延迟）

- 冷构建 1 次全量编译 ≈ 5.3s（bootstrap 全注册表）；同代命中 ≈ 20ms
  （其中大头是结构键的 host 摘要读取）；`builds=1, hits=N`（`availability_cache_stats`）。
- 同代命中全量 compiler 调用次数 = **0**（`test_c13` 断言计数不增长）。
- 重建并发 = **1**（10 线程同时冷启动，`calls['n'] == 1`，其余 coalesced）。
- 同输入确定性：同代快照对象同一（`is` 断言）；selector 决定性排序既有纪律不变。
- 内存：缓存持有 1 份快照 + 整数计数；无会话数相关增长（无 per-session 缓存）。
- 逐 turn 成本：select() 内一次 O(键) 比对 + O(候选) 分类（无网络/无全量扫描）。

## 已知边界（如实）

- C02 的「全 nodata」分支在 G06 实现中已处理（valid_count=0 → None 而非 NaN）；
  本线未新增独立用例，属既有覆盖（上表标注）。
- Pi 扩展宿主（index.mjs setActiveTools）行为不在本地测试面（vendor JS）；
  Python 侧以 marker 内容断言（真实 turn 名单），扩展侧由既有 surface 套件覆盖。
- 未运行：全仓 3 万级 pytest（资源纪律；按影响面选择）、Playwright/浏览器面、
  线上 CI（按任务书不等待）。
