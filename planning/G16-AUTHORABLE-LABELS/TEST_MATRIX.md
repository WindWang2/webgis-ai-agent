# TEST_MATRIX — G16-AUTHORABLE-LABELS（base 816705218）

本地真实执行（worktree: goal-g16-authorable-labels-20261110T163415Z；Python = 主 venv 3.13；FE = pnpm vitest）。
基线既有失败（干净 BASE_SHA 复现，与本分支无关，未触碰）：
- `tests/cartography/test_catalog_docs.py::test_catalog_docs_fresh`（theme-palette-catalog 漂移）
- `frontend tests/e2e-lanes/changed-lanes.test.ts` j13 漏登记（G14 PR #2075 同报）

## 验收场景 → 证据

| 场景 | 证据（测试/命令） |
|---|---|
| 选中三所学校并固定名称 | `tests/unit/test_label_authoring_tools.py::test_pin_three_schools_then_hide_one`（pin×3 一笔原子 → 落库 → 刷新读回 origin=agent）；runtime: `runtime.label-authoring.test.ts` pinned 子层 |
| 两个固定标签互相重叠 | 求解器 `test_label_authoring_solver.py::TestUserWins` + golden `authoring_pinned_conflict`（双 pinned 恒放置 + conflict 旗 + stats.pinned_conflicts；导出诊断 `label_pinned_conflict`） |
| 拖动标签后取消/确认 | `label-editor.test.tsx::拖动只提交一笔`（20 次 move 零服务端、pointerup 一笔）；Esc 取消 = `registerEscapeHandler` 路径 |
| 鼠标与 Pi 编辑同一标签 | `test_label_authoring_mutation.py::test_concurrent_revision_conflict_surfaces`（CAS 过期 superseded/拒绝，用户最新保留）+ 两入口同一意图（WP03/WP10 共用 PatchLayerLabelAuthoringIntent） |
| 隐藏再不同 reset | `test_label_authoring_model.py::TestResetScopes`（作用域互斥）+ `test_label_authoring_mutation.py::test_reset_scopes_are_independent`（引擎真实验径）+ UI `label-editor.test.tsx` reset 四按钮 |
| 缩放/旋转/96/300 DPI 导出 | `test_label_authoring_export.py::TestUnitsAndDpi::test_offset_em_dpi_proportional`（em canonical，比例一致）；`test_offset_never_absolute_screen_px` |
| 粗道路与行政边界 | `test_thick_road_ink_avoided_under_geometry_v1`（单粗线位移避让/双线夹击抑制+披露；legacy 照放）+ `test_thin_road_still_leaves_candidates` + `test_long_road_not_giant_bbox` |
| 面内标签及带洞多边形 | `test_polygon_fill_does_not_block_interior_boundary_does`（填充不拦区内、边界是真墨迹）；洞=外环 only（保守，ADR-0289 披露） |
| 相同 feature ID 不同源 | 覆写键 = 层嵌套（构造上跨层隔离）；求解器 `test_same_feature_id_different_layer_not_exempt` |
| 数据替换/删除/字段移除 | stale: `test_inspect_stale_reason_with_source`（revision 变化照常应用+披露）；orphan: `test_orphan_override_disclosed_not_rendered_elsewhere` + runtime orphan 事件；field_missing: `test_text_override_survives_missing_field` + inspect 词表 |
| 密集图超障碍/标签预算 | `test_obstacles_budget_disclosed_when_truncated`（label_obstacles_budget 披露）+ 求解器预算豁免测试；性能 96ms/88ms（1800 点） |
| 中文/英文混排与非 BMP | `test_label_authoring_model.py::test_text_codepoint_cap`（emoji 120 code point 不半截）+ 既有 CJK wrap 回归 + 控制字符拒绝 |
| 真实 PNG/SVG/PDF | SVG: `compile_mapspec_to_svg_detailed` 全部导出测试（固定/隐藏/覆写/callout/障碍在 `<g class="mapspec-labels">`）；TS 孪生同构 9 例；PDF = svg2pdf 消费同一 SVG labels 组（既有 PDF 回归绿）；PNG = live 画布捕获（runtime 子层渲染测试） |
| 老 MapSpec 和 circle 障碍模式 | `test_legacy_spec_byte_stable_without_policy`（policy 缺省 ≡ circles_legacy 逐字节）+ 求解器 legacy 兼容锁定 + golden corpus 旧 fixture 全绿 |
| “为什么这个标签没显示” | `test_label_authoring_tools.py`（hidden_by_user/stale_override 带源；placement 族 not_evaluated 不冒充 live） |

## 测试清单（新增）

Backend（pytest）：
- `tests/cartography/test_label_authoring_model.py`（39）
- `tests/cartography/test_label_authoring_solver.py`（16）
- `tests/cartography/test_label_authoring_export.py`（21）
- `tests/unit/test_label_authoring_mutation.py`（10）
- `tests/unit/test_label_authoring_tools.py`（6）
- golden corpus `label_collision/authoring_*.json`（4 fixtures，双端差分）
- mutation parity corpus +2 case（golden 再生成）
- schema/词表钉（v6/v15/v16/v19/carto_ir corpus/component_graph/contracts 等）随 v2.1 前移

Frontend（vitest）：
- `lib/mapspec-runtime/label-authoring.test.ts`（6）
- `lib/mapspec-runtime/runtime.label-authoring.test.ts`（9）
- `lib/mapspec-compiler/mapspec-to-svg.authoring.test.ts`（9）
- `lib/mapspec-compiler/label-solver.test.ts` authoring 段（+7）
- `components/map/label-editor.test.tsx`（6）

## 执行命令与结果（2026-11-11）

- `python -m pytest tests/cartography/ tests/unit/test_label_authoring_*.py tests/unit/test_mutation_registry_consistency.py -q` → 3470+ passed / 2 skipped（唯一失败 = catalog_docs 基线）
- `pnpm vitest run`（frontend）→ 5092+ passed（唯一失败 = changed-lanes j13 基线）
- `pnpm tsc --noEmit` → 0 error；`eslint <新文件> --max-warnings 0` → 0
- 性能（同机、单遍）：dense 1800 点 + 40 长线 + 21 覆写：legacy 96ms / geometry_v1 88ms
