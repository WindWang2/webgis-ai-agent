# G34 TEST MATRIX — 真实验证台账

验证层级分别记录：**Pi 子进程 / 后端服务 / 真实 PDF-SVG-PNG 产物 / 浏览器**。分段通过不自动标成全链通过。

证据文件均在 `planning/G34-PRINT-LAYOUT-AUTHORING/evidence/`。

| ID | 场景 | 层级 | 命令 / 断言 | 结果 |
|---|---|---|---|---|
| T01 | 旧 spec 无 plan → 导出字节稳定 | 后端 | `test_t01_legacy_export_bytes_unchanged_without_plan`（SHA-256） | ✅ |
| T02 | `print_layout.v1` validate / 未知版本与未知字段拒绝 | 后端 | `test_t02_versioned_model_rejects_unknown_version_and_fields` | ✅ |
| T03 | mm/pt/逻辑px/采样px corpus + TS/Python parity | 后端+前端 | `test_t03_units_golden_parity_and_four_planes` + `units.test.ts` | ✅ |
| T04 | 标题 x=15mm 宽=180mm → SVG 几何 oracle | 产物 | `test_t04_title_mm_geometry_oracle_in_real_svg`（反算 mm） | ✅ |
| T05 | 96/150/300 DPI 同版式 | 产物 | `test_t05_dpi_physical_size_constant_and_sample_px_varies` | ✅ |
| T06 | PDF `@page` mm + 文本层 + 字体不被全局 CSS 覆盖 | 产物 | `test_t06_pdf_page_mm_text_layer_and_authored_font`（pypdf mediabox + 字体栈首族） | ✅ |
| T07 | 长中文 + 拉丁长词混排 | 后端+产物 | `test_t07_long_cjk_latin_wrap_real_lines_no_truncation` | ✅ |
| T08 | 缺字 / 不存在 weight | 后端 | `test_t08_missing_glyph_and_unavailable_weight_are_explicit` | ✅ |
| T09 | 图例两列含 nodata/范围外，零 slice | 产物 | `test_t09_legend_two_columns_complete_with_nodata_and_out_of_range` | ✅ |
| T10 | 图例扩框/续排/阻断三态，绝不 slice | 后端+产物 | `test_t10_legend_overflow_expand_continue_block_never_slices` | ✅（计数级；续排几何见 REVIEW 开放项 2） |
| T11 | 比例圆三数量级半径比值 | 产物 | `test_t11_proportional_symbols_share_one_scale_across_columns` | ✅ |
| T12 | map slot contain/crop 等比 + effective extent | 产物 | `test_t12_map_slot_contain_crop_uniform_scale_effective_extent` | ✅ |
| T13 | 混合数值栅格 + 矢量地图 | 产物 | `test_t13_mixed_export_svg_consumes_same_authoring_plan` | ✅ |
| T14 | 多画幅产品页放纸面 | 后端 | `test_t14_product_page_overlay_uses_same_plan_without_second_runtime` | ✅ |
| T15 | 三页图册重排 + 关一页 | 后端+产物 | `test_t15_three_page_atlas_reorder_disable_keeps_binding_and_toc` | ✅ |
| T16 | 模板页级覆盖后复位 | 后端 | `test_t16_template_reset_only_clears_g34_overrides` | ✅（只清 override；模板前组件基线不恢复，见开放项 4） |
| T17 | 动态页标题数据缺失 | 后端 | `test_t17_dynamic_binding_missing_shows_visible_placeholder` | ✅ |
| T18 | 应用前 revision 变化 | 后端 | `test_t18_stale_revision_refused_without_mixing_pages` + `test_t28_foreign_mutation_rebinds_instead_of_deadlock` | ✅ |
| T19 | 页面切换旧回包不覆盖新页 | 前端 | `contracts.test.ts`（reducer generation guard） | ✅（reducer 级） |
| T20 | 保存 → 重开 | 后端+前端 | `test_t20_plan_survives_mapspec_document_round_trip` | ✅ |
| T21 | Pi 与 UI 同一版式同一 service/CAS/锁 | 后端+Pi | `test_t21_pi_and_ui_operations_share_one_service_and_result` + **journey** `ui_sees_pi_write` / `ui_write_after_pi` | ✅ |
| T22 | exporter 忽略 component frame → oracle 必失败 | 产物 | `test_t22_exporter_ignoring_component_frame_fails_oracle` | ✅ |
| T23 | 取消 / 字体不可用 | 后端 | `test_t23_font_unavailable_and_cancel_are_explicit_terminal_states` | ✅ |
| T24 | 对齐/等距一次原子 + 锁不动 | 后端+前端 | `test_t24_atomic_align_distribute_lock_and_undo` + `test_t29_locked_component_cannot_be_deleted_or_retargeted` | ✅ |
| T25 | Pi 真实子进程 + 生产扩展 + dispatch + 本域服务 | Pi 全链 | `PI_JOURNEY_E2E=1 pytest tests/integration/test_pi_g34_print_layout_journey.py`（真实 node 子进程 + stub LLM + uvicorn 生产 app） | ✅ 1 passed（62s，`pytest_pi_journey.log`） |
| T26 | 前端 tsc + vitest + i18n 双 locale | 前端 | `tsc --noEmit`；`vitest run lib/print-layout/*.test.ts test/i18n/key-completeness.test.ts --maxWorkers=2` | ✅ tsc 0 error；20 tests passed |
| T27 | 浏览器 E2E（纸面预览/应用/重开/导出） | 浏览器 | Playwright `--workers=1` | ⚠️ **未运行**（本环境无 Playwright 浏览器二进制，不标绿） |
| T28 | 长文本/多栏/混合页/图册上限耗时与内存 | 后端 | 真实规模探针 | ⚠️ 未做（预算边界见 `print_layout_schema` 硬帽：32 组件 / 20 页 / 2KB 文本） |
| T29 | 锁组件不可被 replace_plan 删除 / 族后缀绕过 / 图例改动 | 后端 | `test_t29_*`（零写入断言） | ✅ |
| T30 | apply_template 不得清空锁组件 | 后端 | `test_t30_*` | ✅ |
| T31 | 导出 SVG 声明物理 mm 尺寸 | 产物 | `test_t31_exported_svg_carries_physical_mm_size` | ✅ |
| T32 | 页级 override 不可改锁组件 | 后端 | `test_t32_page_override_cannot_move_locked_component` | ✅ |
| T33 | 缺字度量用兜底族真实 advance | 后端 | `test_t33_missing_glyph_measures_with_controlled_fallback` | ✅ |
| T34 | 首建路径 `initialize=True` 真落盘 | 后端 | `test_t27_apply_initialize_commits_first_plan` | ✅ |

## 汇总

- G34 自有套件：`34 passed`（`pytest_g34_round3.log`）
- Pi 子进程旅程：`1 passed`（`pytest_pi_journey.log`）
- cartography 域回归：`pytest_cartography_regression3.log`
- 前端：`tsc` 0 error；vitest 20 passed

## 运行约束（实测生效）

- pytest：主工作区 `.venv/bin/python -m pytest ... -q --no-cov`（单进程；`--no-cov` 避免全仓 coverage 扫描）
- **必须** `TMPDIR=<worktree>/.g34-tmp`：本机 `/tmp` 是 10MB tmpfs，不设会出一批假 error
- **必须** `USE_REDIS=0`：无 Redis 时锁降级为 inprocess；否则 `LockDegradedError`
- **必须** `CODEBUDDY_SAFE_DELETE_ENABLED=0`：本沙箱的 safe-delete 守卫会拦测试 teardown 的 `rmtree`（环境产物，非代码缺陷）
- Vitest 4.1.11：`node node_modules/vitest/vitest.mjs run <path> --maxWorkers=2`
- 重阶段统一锁：`flock /home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock <cmd>`
