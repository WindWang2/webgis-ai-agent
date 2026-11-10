# G19-TYPED-FIELD-COMPUTE — TEST_MATRIX

状态口径：`passed` / `partial` / `blocked`（附原因）/ `n/a`。命令与实际输出逐行记录，不把旧报告复写为本轮结果。所有命令在 worktree cwd、主 `.venv` 解释器下执行，固定 `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1`、单进程 `-p no:cacheprovider --no-cov`。

## 基线（轮 0，干净 `origin/master @8167052` 复现）

| 项 | 命令 | 结果 |
|---|---|---|
| 既有 preparation 小旅程 | `pytest tests/data/test_preparation_plan_v1.py -q --no-cov -p no:cacheprovider` | **24 passed / 9 failed，78.48s**；9 失败均为 `dataset_semantics/store.py:252` 写 `dsd-v1:<sha>.json` → WinError 123（Windows 冒号非法）。既有环境缺陷，非本任务引入；不删测试/不放宽容差 |
| 环境 | 主 `.venv`：pydantic 2.13.5 / pytest 9.1.1 / fastapi 0.141.1 / pyproj 3.8.0 | ✅ |
| 前端 `tsc --noEmit` | `frontend/node_modules/.bin/tsc --noEmit --pretty false` | 8 个错误**全部**位于未改动的 `lib/map-kit/scene3d/*`（`three` 模块缺失 + implicit any）；field-compute 新增文件 **0 错误**。既有基线失败 |

## 本轮真实执行

| 项 | 命令 | 结果 |
|---|---|---|
| 核心回归（IR/解释器/服务层） | `pytest tests/data/test_field_compute_v1.py -q --no-cov -p no:cacheprovider` | **44 passed（139.57s）**；另加 selection 过期/可靠命中用例后 `-k scope` **1 passed** |
| Pi 工具面（真实 `ToolRegistry.dispatch`） | `pytest tests/unit/test_field_compute_tools_v1.py -q --no-cov -p no:cacheprovider` | **6 passed（79.18s）** |
| API 契约门禁 | `pytest tests/quality/test_api_compatibility.py tests/test_api_docs_drift.py tests/test_endpoint_scope_matrix.py -q --no-cov` | 首轮 3 failed（生成物过期）→ 刷新生成物后 **23 passed（70.73s）** |
| 生成物刷新 | `API_SNAPSHOT_UPDATE=1 pytest tests/quality/test_api_compatibility.py::test_snapshot_matches_and_no_breaking_changes`；`python scripts/gen_api_docs.py`；`python scripts/generate_endpoint_scope_matrix.py` | 快照/api-docs/矩阵三个生成物全部随本 PR 提交 |
| 契约帮助文档 | `python app/services/data_preparation/field_compute/gen_help.py` | 由 `function_help()` + `contracts` + `CANONICAL_UNITS` 生成 `docs/field-compute-contract.md`（46 行表格） |
| i18n | Python 比较 zh/en `fieldCompute.json` | **137 / 137 键完全一致** |
| ruff | `ruff check`（全部新增/改动 Python 文件） | **All checks passed** |
| `git diff --check` | `git diff --check` | 通过（仅 `endpoint-scope-matrix.csv` 一条 CRLF 提示，非错误） |
| 前端组件测试 | `vitest run components/sidebar/data-preparation/field-compute-panel.test.tsx --pool=threads --maxWorkers=1` | **6 passed（13.17s）**：选数据→describe→写公式→预览→执行→结果表/入图、generation 失效、迟到响应丢弃、编译错误 code/hint、键盘可操作。首轮 forks pool 曾 worker 超时，改用 threads pool + 复用主工作区依赖的目录联接后真实跑通 |
| 线上 CI | — | **不等待、不轮询**（CLAUDE.md:41 + 任务书 A） |

## 必须通过的用户与故障场景（任务书 §5）

| # | 场景 | 状态 | 证据 |
|---|---|---|---|
| 1 | 人口÷平方千米面积：值/单位/分子分母/输入版本可复核，可用于制图 | ✅ passed | `test_full_journey_and_original_immutable`（密度 100.0、`measurement_kind=density`、`derived_from` 分子/分母、`anchor.content_hash`、既有 `CartographyService.classify` 可分级）、`test_static_unit_density_evidence` |
| 2 | 变化率含零基期/缺失：error/null 策略，行数与原因保留，未伪造 0% | ✅ passed | `test_if_unselected_branch_does_not_create_error`、`test_division_by_zero_null_and_error_strategy`（`DIVISION_BY_ZERO` + 有限示例 + abort 终态） |
| 3 | 多列依赖确定顺序；循环依赖执行前拒绝 | ✅ passed | `test_cyclic_and_forward_reference_rejected` + `test_full_journey`（3 列链式） |
| 4 | 中文/空格字段名安全引用，不被解析为代码 | ✅ passed | `test_chinese_and_spaced_field_names_are_safe_references`、`test_host_code_attempts_rejected` |
| 5 | 行政区代码 `00123` 前导零保留 | ✅ passed | `test_admin_code_leading_zero_and_unicode`（`000123`）+ 服务层回读 |
| 6 | 布尔/空串/null/数值混合：严格契约，不自动转换 | ✅ passed | `test_bool_empty_string_null_are_not_numeric`（`TYPE_MISMATCH` / `EMPTY_STRING_NOT_ZERO`；空串不是 null） |
| 7 | 大整数前后端往返：保精度或明确拒绝，不静默舍入 | ✅ passed | `test_big_integer_precision_not_silently_rounded`（>2^53 以文本写出 + `precision_policy=bigint_as_string`、字面量编译期 `UNSAFE_INTEGER`、回读为原字符串）；输出采用文本载体，不经过 JS Number 解析，因此不存在静默舍入；UI 组件测试通过。真实浏览器 JSON.parse 往返未单独跑（浏览器渲染服务不可用） |
| 8 | if 未选分支存在除零：分支不求值，结果/错误数符合已知答案 | ✅ passed | `test_if_unselected_branch_does_not_create_error`（errors=0） |
| 9 | 时间跨 UTC 日期边界：声明时区/格式，输出与重放一致 | ✅ passed | `test_datetime_utc_boundary_leap_day_and_invalid`（`+08:00`、跨日→12 月、闰日有效、无效日期 2 例） |
| 10 | 平方米与平方千米等价计算：结果一致，单位元数据未丢 | ✅ passed | `test_unit_conversion_equivalence`、`test_percent_fraction_scale_is_explicit`（RATIO 标尺方向修复） |
| 11 | CRS 未知时面积阻断且给真实原因，普通文本列不受影响 | ✅ passed | `test_crs_unknown_blocks_geometry_but_not_text`、`test_geometry_blocked_in_service_does_not_break_other_columns`（`CRS_UNKNOWN` / 3857 `CRS_NOT_METRIC`） |
| 12 | 选择列表被截断或过期：不把样本当全量，提示重新选择/可靠谓词 | ✅ passed | `test_scope_predicate_and_selection_reliability`（`truncated`→`SELECTION_UNRELIABLE`；revision 不一致→`SELECTION_STALE`；完整可靠集精确命中 2 行、全体 4 行保留） |
| 13 | 只对部分行计算：其他行按明确策略保留，未缩小数据集 | ✅ passed | `test_selected_rows_keep_full_dataset`、`test_scope_predicate_and_selection_reliability`（result 4 行） |
| 14 | 预览后源/schema 已改变：旧计划拒绝，刷新预览可继续 | ✅ passed | `test_stale_plan_after_source_change_rejected`（`STALE_PLAN` + 新计划 9 行预览可继续） |
| 15 | 第二批读取时源被编辑/刷新：固定快照或检测中止，不发布混合 revision | ✅ passed | `test_snapshot_changed_mid_execution_aborts`（发布前二次核对 `SNAPSHOT_CHANGED`，收据未登记）+ `test_anchor_detects_content_and_schema_change` |
| 16 | 模型试图导入模块/访问文件：契约校验拒绝，主进程无副作用 | ✅ passed | `test_host_code_attempts_rejected`、`test_invalid_formula_returns_typed_code_no_host_side_effect` |
| 17 | 极深 AST/超长字符串：预算内结束，给资源边界原因 | ✅ passed | `test_ast_depth_and_long_string_budgets`、`test_unsafe_integer_literal_rejected` |
| 18 | 计算中取消或写入失败：无可见半成品，原件及其他结果不变 | ✅ passed | `test_cancel_and_write_failure_publish_nothing`（`CANCELLED`）、`test_write_failure_is_typed`（`STORE_FAILED`）、原件属性断言 |
| 19 | 同计划重复提交：返回已完成成果或安全恢复，不重复登记 | ✅ passed | `test_full_journey_and_original_immutable`（同 `output_ref` + 同 `created_at`） |
| 20 | 项目保存后重新打开：真正找回数据与公式/来源，过期 session ref 不冒充持久成功 | ✅ passed（本工作台语义） | `test_full_journey`（`persistence=project_artifact`、磁盘 `project_manifest.jsonl` 回读公式/来源/output_ref）；未接入 G18 项目 DB 导出（属其所有权） |
| 21 | UI 与 Pi 用同一公式：等价结果，均能继续查表和制图 | ✅ passed | 后端 `test_dispatch_preview_apply_result_and_next_turn_field`（真实 `ToolRegistry.dispatch` + 同公式 service 等价 + 真实字段名经 `CartographyService.classify` 可分级）；前端 **6 passed** 组件测试覆盖「选数据→写公式→预览→生成→结果表/入图」；两侧调用同一 `/api/v1/field-compute/*` service。真实地图渲染回执仍 `not_evaluated`（见下） |

## 安全 / 恢复 / 规模（WP11）

| 项 | 状态 | 证据 |
|---|---|---|
| 表达式安全边界（import/file/attr/动态代码） | ✅ passed | 7 个对抗输入参数化 + 工具层回归 |
| 输入 revision 冲突 | ✅ passed | `test_stale_plan_after_source_change_rejected` / `test_owner_change_and_fingerprint_drift_rejected` |
| 取消、重复提交、owner 变化、断开后恢复 | ✅ passed | 对应 4 个服务层用例；幂等收据可重放 |
| 恢复后仍能解释公式与来源；原件属性/几何不变 | ✅ passed | `test_full_journey`（深拷贝快照比对原件） |
| 失败任务不污染已完成派生成果 | ✅ passed | `test_snapshot_changed_mid_execution_aborts`（`find_receipt=None`） |
| 批大小不变性 | ✅ passed | `test_batch_size_invariance`（1 / 7 / 100 三档结果逐字节相同） |
| 带洞/多部件面/跨经线 | ✅ passed | `test_geodesic_area_holes_multipart_and_dateline`（Geod + 洞标记 + dateline 披露） |
| 有界基准（机器/输入量/峰值内存/耗时） | ⚠️ partial | 仅记录实测命令耗时（44 项 139.57s、6 项 79.18s、23 项 70.73s），输入量 ≤37 行合成夹具；**未做万行级峰值内存基准**（避免用小样本承诺千万行能力，任务书要求如实报告机器/输入量/耗时，不虚构） |

## 未运行 / 环境阻塞（如实披露）

1. **前端全量构建 / 全量 Vitest / Playwright 浏览器旅程**：未运行（资源纪律与同机并行任务限制）。定向组件测试 6 passed、`tsc --noEmit` 新增文件 0 错误、i18n 137/137 一致；不把未跑项目写成通过。
2. **真实 Pi 进程 + stub LLM 全链路 → MapSpec mutation → 渲染回执**：本环境无 Node extension/runtime 与浏览器渲染服务；已用真实 `ToolRegistry.dispatch` 覆盖工具→服务→成果→既有分类引擎的数据面闭环，`MapSpec`/渲染回执保持 `not_evaluated`，不伪造。
3. **线上 CI**：按授权不等待、不轮询。
4. **既有仓库失败**：preparation 9 项（Windows descriptor 冒号）、前端 8 项（scene3d 缺 three），均在干净基线复现且位于未改动文件，见 `BASELINE.md`。
