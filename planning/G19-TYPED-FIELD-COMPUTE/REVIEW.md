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

**修复后真实执行（第一轮修复 commit `031da7e5c`）**：`ruff check` All checks passed；G19 后端回归 **68 passed**（核心 44 + 评审修复 12 + API 6 + 工具 6）；前端 Vitest **9 passed**；`tsc --noEmit` 新增文件 0 错误（8 个既有 `scene3d/*`）；ESLint 新增文件 0 警告；i18n 141/141；契约门禁 23 passed；`git diff --check` 通过。

---

## 第二轮：独立复审（rereview）

两位独立 reviewer 分别冷读 `c97aa988d..031da7e5c`（第 6 个文档 commit `30eb8fcfb` 未触及核心文件，但已按复审要求核对）：

| Reviewer | 13 项原阻塞 | 新发现 |
|---|---|---|
| `review-rereview`（第一轮同一 reviewer） | 10 已修复 / 2 部分修复（P1-3 收据闸门、P2-8 取消联动）/ 1 以如实声明达成 | **1 个 P1**：最终已发布收据 upsert 失败仍有可见半成品，且稳定键写入失败时 ring 里 `published=True` 会被 `find_receipt` 幂等命中「未发布」成果；**2 个 P2**：cancel 端点对不存在计划返回 500（未包 `_raise_typed`）、`_maybe_upgrade_project` 不清 `PROJECT_SAVE_FAILED` |
| `focused-rereviewer`（全新冷读） | 13/13 已修复（逐条 `file:line` 核对，含 OpenAPI `$ref` 递归比对） | **1 个 P2**：`_CANCEL_REQUESTS` 无界 `set`，对非执行中计划的 cancel 会永久残留键，并让同一 plan 的后续重提第一批就误判 `CANCELLED` |

两位 reviewer 均未发现「把未跑项目写成已验证」；同时指出两处证据偏差：`test_receipt_gate_failure_writes_nothing` 只覆盖草稿闸门失败（不覆盖最终 upsert）、`test_cancel_endpoint_stops_execution` 名为 endpoint 实为服务层直调。均已在本轮修正。

## 第三轮：修复（commit 见 PR）

| # | 修复内容 | 位置 | 回归 |
|---|---|---|---|
| 1 | 发布面探测：`probe_publish()` 在草稿闸门处同时探测 `field_compute/` 与 `field_compute/by_key/` 可写（mkdtemp+fsync+remove），把稳定键失败面提前到 store 之前 | `receipt.py`、`service.py` | `test_publish_probe_failure_writes_nothing` |
| 2 | 稳定键**先写**、ring **后写**；ring 失败即删除刚写的稳定键 ⇒ 「任一次写入失败都不会留下已发布副本」，杜绝「未发布成果被幂等命中」 | `receipt.py` | `test_final_receipt_failure_rolls_back_visible_result`（断言 `find_receipt` 为 None） |
| 3 | 最终 upsert 失败**回滚全部可见面**：`delete_ref` 撤销 session ref、`_remove_from_project_sync` 撤销项目清单条目；回滚不完整时在错误 detail 如实列出残留码 | `service.py` | 同上（断言 `sdm.get` 为 None、项目清单为空） |
| 4 | `_maybe_upgrade_project` 升级成功时清除 `PROJECT_SAVE_FAILED` 并检查 `append_receipt` 返回值（失败仅告警，不清空已真实保存的项目） | `service.py` | `test_upgrade_clears_stale_project_save_failure` |
| 5 | 取消语义：`_ACTIVE_EXEC` 在**取锁之前**登记、`finally` 清理；`request_cancel` 仅对活跃执行返回 True；`_CANCEL_REQUESTS` 改为 `key→monotonic` 字典 + 300s TTL 剪枝 | `service.py` | `test_cancel_during_execution_stops_it`（并发、批间检查）、`test_cancel_on_idle_or_finished_plan_is_noop`（False + 重提不受污染） |
| 6 | cancel 端点包 `try/except (CompileError, ExecError) → _raise_typed`；文档说明「仅执行中登记，空闲/已结束返回 `cancelled=false`」 | `routes/field_compute.py` | `test_unknown_plan_is_typed_404`、`test_idle_plan_cancel_is_false_and_not_poisoned`（真实 ASGI） |
| 7 | `preview` 与 `execute` 状态机对齐：`cancelled` 是可恢复态，只有 `executed`/`rejected` 终态 | `service.py` | `test_preview_allowed_after_cancel` |
| 8 | 前端：入图要求 `features.length > 0`（合法空集合不再报成功）；取消结果三态（`true` 已请求 / `false` 服务端无活跃执行 / `null` 未确认）分别用 `cancelRequested` / `cancelNoActive` / `cancelLocalOnly` 文案，不再无条件宣称已服务端取消 | `field-compute-panel.tsx`、两个 i18n json（143 键） | 组件测试 6 项 + i18n 门禁 10 项 |
| 9 | 测试证据修正：cancel 端点补真实 ASGI 覆盖；用例数按实际统计（44 + 17 + 8 + 6 = 75），删除「45 核心 / 68 passed」的算术偏差 | `tests/data/test_field_compute_api_v1.py`、`TEST_MATRIX.md` | 见下 |

## 第三轮验证（真实执行）

| 项目 | 命令 | 结果 |
|---|---|---|
| 后端 G19 全量 | `pytest tests/data/test_field_compute_v1.py tests/data/test_field_compute_review_v1.py tests/data/test_field_compute_api_v1.py tests/unit/test_field_compute_tools_v1.py -q --no-cov` | **81 passed**（44 + 23 + 8 + 6），38.81s（`--basetemp` 隔离，避免 pytest 清理触发宿主 safe-delete 守卫） |
| 契约门禁 | `pytest tests/quality/test_api_compatibility.py tests/test_api_docs_drift.py tests/test_endpoint_scope_matrix.py -q` | **23 passed**（快照 diff 仅 cancel 端点 description 一行） |
| 前端 | `vitest run field-compute-panel.test.tsx lib/api/field-compute.test.ts test/i18n/key-completeness.test.ts --pool=threads --maxWorkers=1` | **21 passed**（组件 8 + 错误解析 3 + i18n 10） |
| 静态 | `ruff check`（全部新增/改动 Python）、`tsc --noEmit`、`eslint --max-warnings 0` | All checks passed；新增 TS 0 错误（8 个既有 `scene3d/*`）；ESLint 0 警告 |
| 空白/一致性 | `git diff --check`、i18n zh/en 键集合 | 通过；143/143 一致 |

## 第三轮复审（rereview）结果

独立 reviewer（全新冷读，未参与实现）逐项核对 4 个遗留项，结论：前三项已修（发布原子性 / cancel typed 404 / 升级清理），但新发现 **3 个功能性 P2 + 2 项证据映射偏差**：

| # | 发现 | 复核 |
|---|---|---|
| P2-1 | `_execute_locked` 内部 `finally` 无条件 `pop` 取消键：排队执行时先完成者会清掉取消，队列中下一次照常执行 | 已改为「仅当本次是最后一个活跃执行（`_ACTIVE_EXEC<=1`）才 pop」；补 `test_queued_execution_cancel_is_not_lost`（两并发任务都必须 `CANCELLED`） |
| P2-2 | `_maybe_upgrade_project` 失败分支不再追加 `PROJECT_SAVE_FAILED`，幂等命中后才失败的旅程会显示 `session_ref` 却无降级标记 | 已恢复追加（去重）；补 `test_upgrade_failure_marks_degraded` |
| P2-3 | 最终 upsert 失败时 `_rollback_unpublished` 未删除稳定键；`_append_receipt_sync` 的 ring 失败删除失败时无人重试/披露 | 新增 `receipt.drop_published()`（service 回滚时调用），失败以 `STABLE_KEY_ROLLBACK_FAILED` 写入错误 detail；`_prune_cancel_requests` 不再裁剪有活跃执行的 key。补 `test_ring_write_failure_removes_stable_key`、`test_final_failure_reports_stable_key_residual` |
| 证据-1 | REVIEW 表行 2 用 `test_final_receipt_failure_rolls_back_visible_result` 映射「ring 失败删键」，但该测试 mock 的是 `append_receipt`，没走 `_append_receipt_sync` 真实逻辑 | 新增 receipt 层测试直接驱动 `_append_sync` 抛错并断言稳定键不存在 |
| 证据-2 | 行 8（前端空集合/取消三态）映射到组件测试，但原 6 项并未覆盖这两个行为 | 组件测试新增 2 项：空 `FeatureCollection` → `removeLayer` + `loadIncomplete` warning；`cancelled=false` → `cancelNoActive` 文案 |

**如实边界**：additive 登记（descriptor/provenance/artifact）在最终发布失败后**不删除**（registry 无删除 API），它们指向已删除的 ref，不构成可读成果；原「任一次写入失败都不会留下已发布副本」已改为「尽力回滚且在残留时以 residual 码披露」，不再声称完全无痕。

## 第三轮复审后验证（真实执行）

| 项目 | 结果 |
|---|---|
| 后端 G19 全量 | **82 passed**（44 + 24 + 8 + 6，35.12s） |
| 契约门禁 | **23 passed**（29.42s） |
| 前端 | **21 passed**（组件 8 + 错误解析 3 + i18n 10） |
| 静态 | `ruff` All checks passed；`tsc` 新增 0 错误；ESLint 0 警告；i18n 143/143；`git diff --check` 通过 |

## 第四轮复审结果

独立 reviewer（全新冷读，未参与实现）核对 `9461505c9..2efb7fe03`，结论：

- **4 项全部已修复 — 无阻塞，可以进入 PR**：
  1. 排队取消 — `execute()` 取锁前 `_mark_active`、finally 先 `_unmark_active` 再按 `is_executing` 清理；`_execute_locked` 仅当 `_ACTIVE_EXEC<=1` 才 pop（通过）。
  2. 升级失败降级 — 保留 `session_ref` 且 `degraded` 含 `PROJECT_SAVE_FAILED`（通过）。
  3. 稳定键回滚 — `drop_published` 按 stable_key 删除、`_rollback_unpublished` 失败写 `STABLE_KEY_ROLLBACK_FAILED`（通过）。
  4. 测试证据 — ring 失败用例真走 `_append_sync`、并发取消用例真跑两任务；组件两项真断言行为（通过）。
- 3 条 P3 建议，**已全部落实**：
  1. 幂等短路前先 `_check_cancel`，避免排队期间已取消却被判成功；
  2. 升级失败降级回写台账（`append_receipt`），重开/查询也看到真实状态；
  3. `drop_published` 同时撤除 ring 记录（否则 fallback 仍命中已撤销成果），并补成功删除路径用例；ring 失败用例增加「抛错前稳定键已存在」断言，防假通过。

## 状态

- **独立复审已通过（第四轮）**：4/4 已修复、无阻塞、可进入 PR；残余仅为已披露的非阻塞限制。
- 残余已知项：additive 三类登记不可回滚（仅撤销稳定键/ref/项目清单）；真实 Pi 进程 + 渲染回执、浏览器端旅程、线上 CI 未运行（`blocked`/`not_evaluated`）。
- 交付：PR `#2074`（open，未 merge），分支 head 见 `PR_HANDOFF.md`。
