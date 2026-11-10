# G19-TYPED-FIELD-COMPUTE — REVIEW（独立评审 → 修复 → 复审）

## 第一轮：独立冷读评审

- **Reviewer**：`independent-reviewer`（独立 subagent，**未参与本轮任何核心实现，也非前端实现者**）。启动前已关闭前端实现 subagent，释放实现槽位（同时存活 subagent ≤3）。
- **输入**：base `816705218a5aa6202bf15d745cb31cddb3437f44`、head `c97aa988d`（4 commits / 39 文件 +9436/-189）、完整 diff、`TEST_MATRIX.md`、21 个用户场景、用户能力与范围边界、所有权矩阵。
- **方式**：只读冷读；实际执行 `git diff` / `git fetch` / `gh pr list` / Read / Grep / Python 只读比对 i18n 与 OpenAPI 快照；未重跑重型测试，未复写主 Agent 测试结果为自己的验证。
- **结论**：**当前不可进入 PR**。4 个 P1 + 9 个影响验收的 P2，最小修复集合 9 项（按根因合并）。检查面 12 项逐项给结论，既有仓库问题附基线证据与隔离说明。

### 阻塞项清单（file:line / 影响）

| 级别 | 位置 | 问题 |
|---|---|---|
| P1 | `app/schemas/field_compute_schema.py:47` | 新模型与 `data_preparation_schema` 短名重名 → FastAPI OpenAPI 全局消歧，改写既有 7 端点 `$ref` 与 api-docs 既有 2 行 |
| P1 | `field_compute/service.py:487` | 项目保存失败仍标 `project_artifact` 并谎称可重开找回；`PERSISTENCE_FAILED` 是 dead code |
| P1 | `field_compute/service.py:492` | `append_receipt` 失败时 store 孤儿 ref + 项目清单 + 三类登记已可见 |
| P1 | `field_compute/interpreter.py:617` | `datetime_part` 用计划级 timezone，忽略 `DateTimeValue.timezone`；UTC 计划 + `+08:00` 公式跨 UTC 边界取错月份 |
| P2 | `service.py:394` | 幂等命中吞掉「先 session_ref 后 project_artifact」升级 |
| P2 | `service.py:474` | `computed_rows=len(rows)` 把错误/null/未选中行算成功 |
| P2 | `interpreter.py:706` | `abort` 策略下几何/CRS 阻断终止整次，其他列不可执行 |
| P2 | `field-compute-panel.tsx:381` | 前端取消只 abort fetch，不联动服务端 |
| P2 | `field-compute-panel.tsx:422` | 入图无条件报成功、失败留空占位图层 |
| P2 | `receipt.py:25` | ring 64 / manifest 128 截断导致幂等与历史静默丢失 |
| P2 | `docs/api-docs.md:696` | 生成物刷新改了既有端点行（与 P1-1 同根因） |
| P2 | `service.py:55` | `_EXEC_LOCKS` 只增不删，长驻进程内存泄漏 |
| P2 | `field-compute-panel.tsx:279` | UI 固定单列，多列链式依赖主入口不可达 |
| P2（不单独阻塞） | `field_compute.py:104` | owner 可伪造/默认值不一致，UI↔Pi 复用 plan 必 `OWNER_MISMATCH` |
| P3 | 多处 | 运行时加减单位检查 dead path、测试容差过宽、组件夹具不符、`not_evaluated` 空数组、临时 tsconfig 未清理 |

**既有仓库问题（基线证据，不阻塞）**：`test_preparation_plan_v1.py` 24/9（Windows `dsd-v1:<sha>.json` 冒号 WinError 123）、`tsc --noEmit` 8 个 `scene3d/*` 错误、真实 Pi/渲染/CI 未运行 —— 全部位于未改动文件或如实标 `blocked`，不笼统写成「历史问题」。

## 修复（逐项，附回归证据）

| # | 修复 | 文件 | 回归测试 |
|---|---|---|---|
| 1 | 全部契约模型加 `FieldCompute` 前缀；重生成 3 个生成物；验证既有短名 `PlanOut`/`PlanResponse`/`PlanListResponse` 与 `$ref` 零变化 | `app/schemas/field_compute_schema.py`、`app/api/routes/field_compute.py`、`tests/quality/snapshots/openapi.json`、`docs/api-docs.md`、`docs/dev/endpoint-scope-matrix.csv` | `test_existing_short_schema_names_preserved` + 契约门禁 23 passed |
| 2 | 项目保存移到最终收据之前；失败降级 `persistence=session_ref` + `PROJECT_SAVE_FAILED`，工具 `_persistence_note` 与 UI `fc-project-save-failed` 如实披露 | `service.py`、`field_compute_tools.py`、`field-compute-panel.tsx`、两个 i18n json | `test_project_save_failure_downgrades_persistence` |
| 3 | 发布顺序改为「compute → verify_anchor → **收据草稿闸门(published=False)** → store → additive 登记 → 项目保存 → 已发布收据 upsert」；草稿闸门在 store 之前，失败时零可见半成品 | `service.py`、`receipt.py`、`contracts.py` | `test_receipt_gate_failure_writes_nothing`（断言 `fc` store 从未调用、manifest 为空、无已发布收据） |
| 4 | `datetime_part` 改用 `DateTimeValue.timezone` | `interpreter.py` | `test_plan_utc_but_formula_plus8_crosses_day`（UTC 计划 + `+08:00` 公式，跨日仍得 12 月 / 1 月） |
| 5 | 幂等命中补做 `save_to_project` 并更新收据 | `service.py` | `test_idempotent_upgrade_from_session_to_project` |
| 6 | `computed_rows` 由 `interpreter.BatchResult.computed_rows` 真实统计（无列级错误且至少一列有值） | `interpreter.py`、`service.py` | `test_computed_rows_excludes_error_rows` + 既有 `test_full_journey` 新断言 |
| 7 | `GEOMETRY_BLOCK_CODES` 从 abort 终止中豁免（列级降级，其他列仍执行） | `interpreter.py` | `test_abort_with_blocked_geometry_still_computes_text` |
| 8 | 新增 `POST /field-compute/plan/{id}/cancel` + `service.request_cancel` + `_CANCEL_REQUESTS`；前端取消 fire-and-forget 调 `cancelPlan`，文案如实说明「已请求服务端取消，写入阶段成果仍可能生成，可回查」；入图成功 toast 移入成功分支，失败/空数据 `removeLayer` 并 warning | `routes`、`service.py`、`field-compute.ts`、`field-compute-panel.tsx`、i18n | `test_cancel_endpoint_stops_execution`（含取消后正确恢复重提）+ 组件测试 |
| 9 | receipt 增加按 `(fingerprint,input_ref,content_hash)` 的**稳定键文件**（不受 64 条 ring 影响）；manifest 改为按 project 独立文件且**不截断**；`_EXEC_LOCKS` 改 `WeakValueDictionary`；UI 明确声明「一次编辑一列，多列链式走 Pi/API」 | `receipt.py`、`service.py`、`field-compute-panel.tsx`、i18n | `test_ring_overflow_keeps_idempotency`、`test_manifest_does_not_silently_drop_history`、`test_exec_lock_table_is_weak` |
| 10（附加） | owner 由服务端认证上下文注入、忽略 body 覆盖；降级为提示性元数据，不当权限门禁（隔离在 session 层）；收敛测试容差；修组件夹具为真实常量；`not_evaluated` 恒含；`parseFieldComputeError` 兼容统一错误信封 `data` 并补真实单测；清理临时 tsconfig | `routes`、`service.py`、组件测试、`field-compute.ts`、`field-compute.test.ts` | `test_owner_is_metadata_not_a_gate`、`field-compute.test.ts`（3 项）、i18n 141/141 |

**修复后真实执行**：`ruff check` All checks passed；G19 后端回归 **68 passed**（核心 45 + 评审修复 12 + API 6 + 工具 6，含 `-k scope` 1 项）；前端 Vitest **9 passed**；`tsc --noEmit` 新增文件 0 错误（8 个既有 `scene3d/*`）；ESLint 新增文件 0 警告；i18n 141/141；契约门禁 23 passed；`git diff --check` 通过。

## 复审

- **状态**：待独立 reviewer 复审（用同一 reviewer 的 resume，冷读新 diff 与修复证据，不预设通过）。
- 复审输入：`REVIEW.md` 本表、`test_field_compute_review_v1.py` 12 项回归、`git diff c97aa988d..HEAD`、上述真实命令输出。
- **待填**：复审结论、残余 P0/P1 / 影响验收 P2 是否清零、是否可以进入 PR。

---

## 复审结果（第二轮）

> 待 reviewer 回填；主 Agent 不得自行标记通过。
