# Test Matrix（精确数字，2026-09-10）

后端（venv Python 3.13，worktree）：
- tests/cartography/ 全量：**1029 passed**（含 W1-W11 新增 7 个测试文件）
- tests/unit/test_compiler_parity.py + test_report_service_vector_svg.py +
  test_svg_sanitize.py：全绿（legacy byte-stable 闸未回退）
- pytest --no-cov；耗时 ~10s（串行，低资源）

新增测试文件（counts）：
- test_diagnostics_dead_code_gate.py（25，参数化词表全量）
- test_mapspec_schema_v6.py（20：脏值 corpus/迁移矩阵/canonical golden）
- test_ts_projection_contract.py（5：幂等/导出面/V6 additive）
- test_render_scene_parity.py（11：双语言 fixtures + 组件解析镜像）
- test_legend_model_parity.py（13：fixtures + oracle 一致性 + formatter）
- test_label_collision_export.py（9：差分 + 不变量 + 确定性）
- test_label_collision_export_twin.py（5：孪生集成/抑制/预算/旋转）
- test_publication_chrome.py（6：全片段/单源图例/disabled/absent/bounds）
- test_publication_vector_pdf.py（8：文本可提取/多帧多页/raster 披露/forward 拒绝/SSRF）
- test_vector_pdf_route.py（3：鉴权/结构化错误/真实 PDF 落盘）
- test_publication_perf_a11y.py（3：20k 结构断言/诊断封顶/role+title）

前端（vitest，worktree + 主仓 node_modules）：
- 全量：**290 files / 2762+ tests passed**（W9 后复跑 290/2762）
- 新增：legend-model.parity.test.ts(9)、render-scene.parity.test.ts(7)、
  label-solver.test.ts(9)、spec-frames.test.ts(5)、mapspec-to-svg.collision.test.ts(4)、
  types.contract.test.ts(2)
- tsc --noEmit 全绿；eslint 全绿
