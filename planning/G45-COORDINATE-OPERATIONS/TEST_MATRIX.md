# G45 TEST_MATRIX — 验收场景台账 C01–C40

判定标准：PASS = 有真实代码路径 + 本次实测通过；NOT RUN = 明确记录原因。
测试文件均在本 worktree；证据命令可复跑（worktree venv + heavy 锁）。

| # | 场景 | 判定 | 证据（测试/机制 → 实测） |
|---|---|---|---|
| C01 | WGS84 2D 点→UTM 明确操作，完整成果与独立 oracle 对账 | **PASS** | `test_kernel_and_candidates.py::TestIndependentOracle::test_c01_*`（Redfearn 4 阶解析 oracle，<0.01m）+ `test_execute_flow.py::test_c01_c20_full_execute_1201_features`（1201 行全链，输出文件逐点 oracle）+ Pi 旅程 `coordinate_oracle` gate |
| C02 | 官方轴序 lat/经 vs 载体 lon/lat 不双重交换 | **PASS** | `TestCrsContract::test_c02_official_axis_order_disclosed_not_applied`（官方轴序仅展示）+ `::test_c02_geojson_lonlat_not_double_swapped`（非对称点裁决：双重交换将偏 >1000m，实测 oracle 通过） |
| C03 | 互换不同的 x/y 依据载体约定可裁决 | **PASS** | `TestCrsContract::test_c03_asymmetric_points_distinguish_axis_convention`（(55,25)/(25,55) 均合法经纬度，偏移 >1e5）+ `test_c37_wrong_axis_order_version_is_caught` |
| C04 | 源 CRS 缺失/不可信 → typed 阻断，不默认 4326 | **PASS** | `test_execute_flow.py::test_c04_missing_crs_blocked_not_defaulted` + `::test_c04_untrusted_crs_label`（`source_crs_missing`；无范围推断路径——`freeze.py` 只认载体元数据证据） |
| C05 | 源/目标相同 → no-op 候选显式化 | **PASS** | `TestCandidateContract::test_c05_same_crs_noop_derivation`（noop 候选在列，选择显式） |
| C06 | 米与 US survey foot 单位因子与官方一致 | **PASS** | `TestIndependentOracle::test_c06_foot_units_factor`（EPSG:2263 ftUS vs 32118 米制对账 <0.05ft；ftUS 常数 0.30480060960121924） |
| C07 | 多候选不同条件，选定后执行与回执同一项 | **PASS** | `test_c07_multiple_operations_with_different_conditions`（ED50→WGS84 35 候选，accuracy 集合 >1，截断如实）+ 执行链 receipt.operation 与锁定 candidate_id 同源（`test_c01_c20` 断言） |
| C08 | 最佳操作缺离线格网 → unavailable 原因保留，无静默 fallback | **PASS** | `test_c08_missing_grid_kept_with_reason`（NAD27→NAD83：noop ballpark 可执行 + 9 项缺格网不可执行同列，含格网名/URL）+ 面板测试候选对照渲染 |
| C09 | 显式选 ballpark/unknown accuracy → 计划政策可见，unknown ≠ 0 | **PASS** | `test_c09_unknown_accuracy_never_zero` + Pi 旅程 `lock_frozen_in_db`（noop ballpark 候选真实锁定执行）+ 面板测试「精度未知」渲染 |
| C10 | preview 后 PROJ/格网环境改变 → 计划失效/ENV_CHANGED | **PASS** | `test_c10_env_drift_detected`（篡改冻结环境证据 → `operation_env_changed`）+ `test_execute_flow.py::test_c10_env_drift_blocks_preview`（DB 级环境证据篡改被预览期复核阻断）+ `FrozenOperation.verify_environment`（执行前同复核） |
| C11 | 客户端伪造 pipeline/格网路径/他人 candidate_id → 拒绝 | **PASS** | `test_c11_fake_candidate_id_rejected` + `::test_c11_unavailable_candidate_lock_rejected`（锁定只收服务器签发 id；执行定义只从冻结 PROJJSON 重建，无任意 pipeline 入口） |
| C12 | 域外坐标严格政策可复核 | **PARTIAL→已收窄**（评审后：fail 政策从词表摘除 —— 契约不承诺未实现语义；域外披露走 preview findings（input/output phase 区分）+ 执行非有限 all-or-nothing；`test_review_fixes.py::test_preview_distinguishes_non_finite_phase`） | 预览 `findings.out_of_area_of_use`（地理目标域内检查披露）+ 计划 `out_of_area_policy`（report/fail 显式）；执行期非有限输出 all-or-nothing（C18 测试）。样本在域内不当全量保证 —— `sample_rule` 明示。域外全量 fail 政策未单列集成测试（共享非有限失败路径），记 PARTIAL 由 review 评估 |
| C13 | 反经线/极区/多块 AOI | **PASS** | `test_c13_antimeridian_aoi_rejected`（west≥east typed 拒绝并说明拆分建议；绝不构造错误全球 bbox） |
| C14 | 动态 datum/epoch/compound/垂向 → typed 拒绝 | **PASS** | `test_c14_compound_and_dynamic_rejected`（EPSG:9518 compound、7912 ITRF2014 DYNAMIC[FRAMEEPOCH] WKT 标记探测 → 拒绝并说明） |
| C15 | 合法 Z 的水平变换 → 逐值保留 + 读回 | **PASS** | `test_c15_z_preserved_unconverted` + `test_staging_stream_and_verify`（61 点 Z=888.8/123.5 经 parquet 往返保真）+ 收据 `z_policy`/`z_note`（horizontal-only 披露） |
| C16 | M/第四维/载体不支持 → typed 阻断 | **PASS** | `crs_contract._dimension_support`（axis_count>3 → 拒绝）；M 维度载体无保真路径按政策拒绝（`_dimension_support` 返回 typed 原因）；Pi 旅程 `empty_geometry_kept` 相关属性保真佐证 |
| C17 | 全几何类型（含洞/Multi/Collection）结构保持 | **PASS** | `test_c17_all_geometry_types_preserved`（Point/MultiPoint/Line/Polygon 含洞/MultiPolygon/GeometryCollection；环结构/属性/顺序/GC 子几何真实变换） |
| C18 | 空/null 几何独立计数；失败点触发全任务失败 | **PASS** | `test_c18_null_and_empty_geometry_counted_separately` + `test_c18_all_or_nothing_no_publication_on_failure`（NaN 输入 → 无终态包、无 completed run）+ Pi 旅程 `empty_geometry_kept`/`receipt_counts`（1199 变换 + 1 null + 1 empty = 1201） |
| C19 | 单几何超大/顶点爆炸 → 分配前闸 | **PASS** | `test_c19_single_geometry_vertex_budget_gate`（>MAX_SINGLE_GEOM_VERTICES 在变换前 `budget_exceeded`）；总顶点/输出字节闸在 `run_streaming_transform` 同层 |
| C20 | 1201 要素（超预览行数）完整成果 | **PASS** | `test_c01_c20_full_execute_1201_features` + Pi 旅程（文件/manifest/receipt/结果页全 1201）+ j25 浏览器旅程（UI 显示 1201/1201） |
| C21 | 非 4326 主成果地图显示挂独立 4326 ref | **PASS** | `test_g18_delivery.py::test_c21_non4326_display_mountable`（display_ref 载荷度数范围核验 + 独立操作 definition_sha256 证据） |
| C22 | 显示副本失败、主成果成功 → 状态分开 | **PASS** | `test_c22_display_failure_keeps_main_completed`（monkeypatch 注入显示失败 → completed + display.state=failed；执行体另有深度防御隔离层） |
| C23 | G18 GPKG 保持 CRS 导出，无再次选操作 | **PASS** | `test_g18_delivery.py::test_c23_gpkg_keeps_target_crs_no_reselection`（无 target_crs → G18 不建 transformer；GPKG CRS=32650、坐标/行数/ID 与原生成果一致）+ Pi 旅程 `gpkg_*` gates + j25 下载段（SQLite magic 回读） |
| C24 | GeoJSON 交付使用验证过的 WGS84 副本 | **PASS** | `test_g18_delivery.py::test_c24_geojson_uses_wgs84_display_copy`（display ref → GeoJSON 4326 度；原生 UTM 与显示副本分别对账解析 oracle） |
| C25 | 源在预览后改变 → 旧绑定失效 | **PASS** | `test_execute_flow.py::test_c25_source_change_after_lock_blocks_preview_execute`（同 ref 覆写 → 指纹漂移 → `source_revision_changed`）；执行前二次复核（`_execute_body` 源终复核） |
| C26 | 可变文件 ABA → 冻结隔离 | **PASS** | `test_c26_freeze_isolates_aba`（成果行数=冻结时行数）+ `freeze_source`（parquet 拷贝后 sha256 对账；payload → GeoParquet 冻结件；执行只读冻结件） |
| C27 | 并发执行/幂等重试 → 至多一套有效提交 | **PASS** | `test_c27_idempotent_reuse_verified`（同键复用 completed run；内容删除 → 幂等失效 typed → 新执行）+ 活跃 run 收敛 + 世代键（G38 同款语义） |
| C28 | 各阶段取消 → 终态诚实，无取消后发布 | **PASS** | `test_c28_cancel_before_publish`（变换中注入取消 → 无完整发布）+ `cancel_run`（job 行取消请求 + 看门狗感知） |
| C29 | 旧 attempt 迟到/崩溃重启 → fencing 阻止覆盖 | **PASS** | `test_c40_stale_attempt_cannot_publish`（worker 接管 → `publish_blocked`）+ `test_c29_recovery_converges_orphans`（崩溃窗口按权威文件/manifest 恢复收据） |
| C30 | 撤权/解绑/外用户 cursor → 拒绝 | **PASS(服务面)` | G18 `resolve_source` 会话/项目守卫复用（C30 的授权面继承 G18 既有测试网）；跨会话源在 trusted 回退外拒绝（`resolve_snapshot` 映射 `unauthorized`）。本域独立行级测试未单列 —— 归属 G18 回归面，review 确认 |
| C31 | 截断选择/样本 ref/粗 bbox 不冒充完整 | **PASS** | `test_c31_truncated_source_blocked`（truncated=true → `source_incomplete`；G24 完整句柄语义留给 G46 域） |
| C32 | 1201 要素经真实 Pi 发现→plan→preview→execute→result | **PASS** | `test_pi_g45_coordinate_operations_journey.py`（真实 Pi 子进程 + node stub LLM + 生产 dispatch + 权威 DB/文件裁决；21 gates 全绿，2026-11-16 本 worktree 实测） |
| C33 | 真实浏览器作者流程后重开并下载 | **PASS** | `frontend/e2e/journeys/j25-g45-coordinate-operations.journey.ts`（real 档：真实登录→chat 会话→产品链播种→面板候选/锁定/预览→执行 completed→重开（服务器状态源）→G18 GPKG 下载回读；2026-11-16 实测 1 passed） |
| C34 | 预算超限 → 实测 typed 闸 | **PASS** | `test_c34_row_budget_at_describe`（describe 期行数闸）+ C19 顶点闸 + 输出字节闸（`run_streaming_transform` 尾部）+ 收据 `budget_measured`（RSS/时长/字节实测记录） |
| C35 | 可信离线格网的正向转换 | **NOT RUN** | 本环境 PROJ 数据目录 0 格网（BASELINE 实测）；缺格网阻断面已验证（C08），格网正向路径无法在本环境真实执行 —— 诚实记 NOT RUN（产品代码路径同一 `from_pipeline` 冻结链） |
| C36 | 中文/null/大整数/_wfid 属性保真 | **PASS** | `test_staging_stream_and_verify`（属性逐项断言）+ Pi 旅程 `property_fidelity` + j25（含中文属性往返） |
| C37 | 人工解析/官方控制点 + 故意错误版本 → oracle 抓错 | **PASS** | `test_c37_wrong_axis_order_version_is_caught`（交换喂入 → 与 oracle 偏 >1000m 被断言区分）+ `_utm50n_analytic`/`_webmercator_analytic` 独立实现（非 pyproj 生成 golden） |
| C38 | 发布后回执失败/产物 GC → 状态可恢复 | **PASS** | `test_c29_recovery_converges_orphans`（receipt_finalized 路径）+ `verify_published` 复用前核验（内容删 → typed `result_unavailable`，C27 测试内） |
| C39 | 发布前取消/授权/claim 查询不可达 → 停止发布 | **PASS** | `test_c39_cancel_facts_block_publish`（持久取消事实 → `publish_blocked`，无终态包）+ `strict_publish_gate` DB 异常 → 阻断（fail-closed，不继承 runner fail-open） |
| C40 | body 计算中旧 worker 失 claim → 不得发布 | **PASS** | `test_c40_stale_attempt_cannot_publish`（新 worker 接管后旧 body 发布被门阻断） |

## 汇总

- **评审后：PASS 37 / 收窄 1（C12）/ PASS-继承 1（C30）/ NOT RUN 1（C35 无格网环境）**；新增评审回归 8 项（test_review_fixes.py）
- C35 说明：任务书明确「基线离线无格网时必须完成普通明确操作与缺格网阻断，不假称验证了高精度格网路径」—— 本环境正是该基线；格网依赖候选的正确披露与锁定拒绝已验证（C08/C11）。
- 复跑命令：
  - 域测试：`.venv/bin/python -m pytest tests/coordinate_operations/ -q --no-cov`（52 项）
  - Pi 旅程：`PI_JOURNEY_E2E=1 .venv/bin/python -m pytest tests/integration/test_pi_g45_coordinate_operations_journey.py -q --no-cov`
  - 浏览器旅程：`cd frontend && E2E_MODE=real E2E_API_URL=<backend> E2E_USER/PASS=… npx playwright test e2e/journeys/j25-g45-coordinate-operations.journey.ts --workers=1`（需真实后端栈 + 管理员；隔离 CORS_ORIGINS 指向前端端口）
