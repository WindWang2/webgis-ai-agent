# G41 TEST MATRIX（真实验收证据）

环境：Linux；Python 3.12.15（复用同仓既有 venv，pytest 9.2.0 / pydantic 2.14.2 /
weasyprint 70 / PIL 13）；node v22.22.2 / pnpm 10.34.9 / vitest 4.1.11 / tsc；
`USE_REDIS=false`（同仓 tests/conftest.py 既有钉扎）；`OMP_NUM_THREADS=OPENBLAS_NUM_THREADS=MKL_NUM_THREADS=1`；
pytest 单进程、Vitest `--maxWorkers=2`（4.1.11 无 `--minWorkers`，未写无效参数）；
重阶段导出用同仓统一 `flock`。

## 已执行的测试命令与结果

| # | 层级 | 命令 / 范围 | 结果 |
|---|---|---|---|
| T1 | L1 独立 oracle | `pytest tests/unit/bivariate_reliability/test_oracle.py` | 15 passed（手算 expected，不调生产 assign 生成期望） |
| T2 | L1 合同 | `pytest tests/unit/bivariate_reliability/test_contract.py` | 20 passed |
| T3 | L2 服务集成 | `pytest tests/unit/bivariate_reliability/test_service.py` | 24 passed |
| T4 | L2 工具/路由 | `pytest tests/unit/bivariate_reliability/test_tools_and_routes.py` | 11 passed |
| T5 | L1–L2 合计 | `pytest tests/unit/bivariate_reliability/` | **73 passed**（第 2 轮复审修复后重跑；新增 flip_rows 行映射、clamp 越界桶、无图例层 revert 可达三条回归） |
| T6 | L3 前端组件 | `vitest components/hud/bivariate-reliability-panel.test.tsx` | 5 passed |
| T7 | L3 图例/导出适配器 | `vitest lib/map-kit/g41-canonical-legend.test.ts` | 6 passed |
| T8 | L3 i18n | `vitest test/i18n/key-completeness.test.ts` | 10 passed |
| T9 | L3 前端回归 | `vitest` 14 个受影响文件（bivariate panel / canonical legend / layer-style-panel ×2 / thematic-authoring / thematic-legend / variants-v4 / export-chrome ×3 / vector-svg ×3 / i18n key-completeness） | **140 passed**（第 2 轮修复后重跑；含 `audit840` 的 `session-cursor` 不完整 mock 修复，以及新增「Apply 后仍可继续预览并再次应用」用例） |
| T10 | L4 真实 Pi 两模式旅程 | `PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_g41_bivariate_reliability_journey.py -q -s` | **1 passed（18/18 gates true，`fail: []`）**，固定 pin `v1.5.2-0-gb4afdddf4`；判决原文见下节，详见下节 |
| T11 | L5 真实出版三格式 | `pytest tests/integration/test_g41_publication_readback.py` | **6 passed**（第 2 轮修复后重跑：bivariate + reliability × SVG/PNG/PDF 独立回读、长字段名全文可回读、跨 DPI、拒绝普通色条替代、混合图层「矩阵 + 每条条目都画」；可靠性地图面断言真实 fill-opacity） |
| T12 | 后端回归 | `pytest tests/unit/thematic_authoring/ tests/cartography/test_g34_print_layout_authoring.py tests/cartography/test_v4_cartography_libs.py tests/unit/test_analysis_cartography_converter.py` | **138 passed, 2 skipped**（复审修复后重跑，同值） |
| T13 | 类型 | `pnpm exec tsc --noEmit` | 仅 2 处**既有**错误：`components/chat/cartography-result-card.tsx:24,49`（该文件本次未改，`git diff` 为空）；新代码 0 错误 |
| T14 | i18n 裸 CJK | `vitest test/i18n/no-raw-cjk.test.ts` | 唯一失败为**既有** 4 个文件（`import-workbench.tsx` / `scale-portrayal-panel.tsx` / `thematic-authoring-panel.tsx` / `skill-workbench.tsx`，本次均未改）；新增文件 0 处 |
| T15 | 分层门禁 | `python scripts/check_import_boundaries.py` | 报 9 条**既有**违规（`mixed_map_export.py` / `raster_authoring/delivery.py` / `temporal_media/store.py`，本次均未改）；新增模块 0 违规 |
| T16 | 静态卫生 | `git diff --check`；`python -m compileall` 全部新增 py | 通过 / 通过 |
| T17 | Pi pin | `pi_vendor_identity()` | `entry_built=True kind=bundle`，`head=expected=b4afdddf44e059c67ccf5097e5317d913521b470`，`describe=v1.5.2-0-gb4afdddf4`，`matches_expected_pin=True` |

## L4 真实 Pi 旅程（证据）

链：node stub LLM SSE → `uvicorn(app.main:app)` 生产 lifespan → 固定 pin Pi RPC 子进程
→ `POST /api/v1/chat/stream`（`X-Session-Token`）→ `tool_search` deferred 激活
→ `webgis_bivariate_reliability_{inspect,preview,apply}` → `ToolDispatchService`/`ToolRegistry`
→ 本域服务 → 单笔 `UpsertLayerIntent` → 冻结派生展示 ref + canonical 图例。
**只有 LLM 响应是 stub**；RPC / dispatch / 写入 / 渲染主链均未 mock。

本轮（复审修复后）判决原文：

```json
{"fail": [],
 "observations": {"bivariate_digest_in_sse": true},
 "journeys": {
  "warmup_ok": true,
  "bivariate_inspect_via_pi": true, "bivariate_preview_via_pi": true,
  "bivariate_preview_digest_via_registry_tool": true,
  "bivariate_apply_via_pi": true, "bivariate_map_committed": true,
  "bivariate_joint_counts_real": true, "bivariate_evidence_agent": true,
  "bivariate_derived_ref": true, "bivariate_derived_readback": true,
  "bivariate_source_not_written_back": true,
  "reliability_preview_via_pi": true, "reliability_apply_via_pi": true,
  "reliability_map_committed": true, "reliability_no_fabricated_confidence": true,
  "reliability_missing_bucket": true, "reliability_evidence_agent": true,
  "reliability_authorized_download_readback": true}}
```

18 个 gate（`verdict["journeys"]` 全部 `True`，`fail=[]`）：

| 模式 | gate |
|---|---|
| 公共 | `warmup_ok` |
| 双变量 | `bivariate_inspect_via_pi`, `bivariate_preview_via_pi`, `bivariate_preview_digest_via_registry_tool`, `bivariate_apply_via_pi`, `bivariate_map_committed`, `bivariate_joint_counts_real`, `bivariate_evidence_agent`, `bivariate_derived_ref`, `bivariate_derived_readback`, `bivariate_source_not_written_back` |
| 可靠性 | `reliability_preview_via_pi`, `reliability_apply_via_pi`, `reliability_map_committed`, `reliability_no_fabricated_confidence`, `reliability_missing_bucket`, `reliability_evidence_agent`, `reliability_authorized_download_readback` |

其中 `bivariate_preview_digest_via_registry_tool` 通过**生产 ToolRegistry dispatch** 调同一预览工具，
逐字比对 `candidate_digest`，不是宽松回退。`reliability_authorized_download_readback`
用真实 `owner_token` 调 `get_ref_data_shared` 回读派生展示 ref。

## 场景表覆盖（B01–B38）

| ID | 覆盖 | 证据 |
|---|---|---|
| B01 | ✅ | `test_b01_bivariate_full_chain_produces_real_map_and_derived_ref` + Pi `bivariate_map_committed` |
| B02 | ✅ | `test_b02_two_axes_can_use_different_methods`（每轴 method/unit 独立） |
| B03 | ✅ | `test_b03_manual_breaks_exact_equality_and_legend_consistency` + oracle `test_boundary_rule_hand_computed` |
| B04 | ✅ | `test_b04_legacy_bivariate_layer_is_never_rewritten_by_inspect`（层字节不变、`__biv_class` 不变）+ 前端旧 bivariate 无 cells 仍发降级诊断 |
| B05 | ✅ | `test_b05_constant_axis_and_empty_cells_are_honest`（常数轴 actual=1、空格如实） |
| B06 | ✅ | `test_same_field_twice_rejected` |
| B07 | ✅ | `test_different_units_are_legal_and_independent` |
| B08 | ✅ | `test_value_kinds_and_strict_admission` + `test_missing_reconciliation_and_no_silent_float` |
| B09 | ✅ | 同上 + `PairedScan.reconcile()` 平衡断言 |
| B10 | ✅ | `test_b10_scatter_is_bounded_but_counts_are_exact`（500 要素，散点 50、计数 500 精确） |
| B11 | ✅ | oracle `test_swap_axes_transposes_counts_colors_and_class_index`（非对称 9 色） |
| B12 | ✅ | oracle `test_flip_rows_reverses_cells_colors_and_arrow` + canonical cells 屏幕序/箭头/端点 |
| B13 | ✅ | `test_n4_and_manual_break_count_rejected_not_clamped`, `test_insufficient_colors_rejected_not_clamped` |
| B14 | ✅ | `test_b14_source_with_user_biv_class_is_never_overwritten`, `test_b14_derived_field_collision_is_rejected` |
| B15 | ✅ | `test_b15_source_mutation_during_freeze_is_rejected`（descriptor rev 前后不一致） |
| B16 | ⚠️ 部分 | 非 ref 源 / 纯瓦片 URL → `NO_STRONG_REVISION` typed 拒绝（`test_b38` 同源机制）；「选择 >50 对象需 G24 完整 handle」本任务**不实现选择路径**，只在合同层要求完整冻结源；如实标注未做选择句柄集成 |
| B17 | ✅ | `test_b01`（基准源逐要素断言无派生字段）+ Pi `bivariate_source_not_written_back` |
| B18 | ✅ | oracle `test_reliability_bucket_mapping_hand_computed`（零误差 → b0 / 0.9，不 truthiness） |
| B19 | ✅ | `test_negative_measure_is_invalid_not_low_reliability`, `test_variance_requires_squared_unit` |
| B20 | ✅ | `test_b20_ci_pi_distinct_and_level_in_legend`, `test_ci_and_pi_are_distinct_in_digest` |
| B21 | ✅ | `test_b21_low_gt_high_is_invalid_not_auto_sorted`, `test_invalid_level_rejected_no_auto_95` |
| B22 | ✅ | `test_b20`（非对称区间不强制居中，`noncentered` 披露）；`require_interval_contains_estimate` 可显式要求 |
| B23 | ✅ | `test_b23_no_uncertainty_evidence_is_blocked_with_next_steps`, `test_apply_tool_no_uncertainty_evidence_is_honest` |
| B24 | ✅ | `test_b18_b24_reliability_zero_unknown_and_no_fabrication` + oracle unknown 独立输出 |
| B25 | ✅ | `VisualPolicy` 输出有界 + 单一 `paint.opacity`（不重复乘 alpha）；`direction` 显式；**深色底图真实像素级对比未跑浏览器**，见「未测」 |
| B26 | ✅ | `test_b26_two_sources_without_stable_join_are_not_merged`（合同只接受同一冻结源同一行；不按行序 join） |
| B27 | ✅ | `test_b27_changed_axis_or_error_type_invalidates_preview` + 前端 `invalidates the preview` |
| B28 | ✅ | `test_b28_cas_conflict_preserves_user_decision` |
| B29 | ✅ | `test_b29_shared_expression_member_is_refused` |
| B30 | ✅ | `test_b30_revert_cleans_stale_bivariate_and_second_channel`（含 `_preserve_durable_presentation` 交互坑修复） |
| B31 | ✅ | `test_b31_foreign_owner_read_is_rejected` + Pi `reliability_authorized_download_readback`（HTTP 路由 `require_owned_session`） |
| B32 | ✅ | `test_legend_rejects_plain_colorbar_substitute`（长字段名完整进入服务端 SVG/PDF） |
| B33 | ✅ | `test_dpi_scaling_keeps_same_semantics`（同 mm 版式、SVG/PNG/PDF 独立回读、格子/颜色一致） |
| B34 | ✅ | Pi 旅程双变量 10 个 gate + 生产 dispatch |
| B35 | ✅ | Pi 旅程可靠性 7 个 gate + 受权回读 |
| B36 | ✅ | `test_b36_late_callback_cannot_commit_into_wrong_session` + 前端 epoch/请求 generation + `STALE_PREVIEW` |
| B37 | ✅ | `test_b37_aba_restore_is_detected_by_monotonic_generation` |
| B38 | ✅ | `test_b38_missing_descriptor_is_typed_rejection` + `SOURCE_MUTATION_DURING_READ` |

## 未测 / 阻塞（如实保留）

| 项 | 状态 | 原因 |
|---|---|---|
| 真实浏览器 E2E（Playwright） | **未运行** | 本环境无已安装浏览器可执行文件；前端 vitest（jsdom）+ 生产 SVG/PNG/PDF 回读不能冒充真实浏览器全链通过 |
| 深色底图下 alpha 的真实像素对比（B25 视觉子项） | **未运行** | 同上；已用结构/数值/有界输出断言覆盖语义，不写「同等覆盖」 |
| B16 选择句柄（>50 对象 / MVT 完整 handle / G24 精确重放谓词） | **未实现** | 属 G24/G38 边界；本任务只接受完整冻结 `ref:` 源，非 ref/局部瓦片明确 `NO_STRONG_REVISION` |
| 模板 / 多 frame / atlas 组合导出 | **未测** | G34 额外组合；核心单幅版式已真实验证，不把一条成功路径当全格式支持 |
| 线上 CI 结论 | **不等** | 任务 A 节 + `CLAUDE.md:41`；未轮询 checks、未建 watcher |
| Goal usage budget ≥320M tokens | **宿主未暴露** | 无 `/goal` / `/goal-loop` 工具与额度配置接口；不伪造额度 |
| `pnpm exec tsc --noEmit` 2 处既有错误、`no-raw-cjk` 既有 4 文件失败、`check_import_boundaries` 既有 9 条违规 | **既有失败** | 相关文件本次 `git diff` 为空，属干净 base 遗留；未删测试/未放宽断言/未刷新 golden |

## 资源纪律

- 未并行跑多个重型阶段；首次 publication 与 typecheck/vitest 并发曾触发一次 SIGKILL，改为串行重跑通过（记录为环境实测）。
- 端口/临时目录/会话 id/DATA_DIR 均隔离；未杀他人服务、未 flushall、未删公共缓存。
- 固定 pin 的 Pi 产物从同仓同 pin 的 `g33` worktree 复制到本 worktree 未初始化 gitlink 目录（git 不跟踪内容，diff 干净），未改 `.gitmodules`/部署契约。
