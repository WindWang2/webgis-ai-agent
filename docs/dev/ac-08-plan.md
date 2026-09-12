# ac-08 出版级导出 V10 · 执行计划（planning-with-files）

- 分支：`adaptive-cartography/08-publish-export` @ worktree `webgis-wt-ac-08`
- 基线：origin/master `1fd4b035`；ADR-0157；无迁移
- 任务书：主仓 `goals-ac/08-publish-export.md`
- P0 勘察：`docs/dev/ac-08-export-recon.md`（含复核纪要 + DPI 基线实测）

## 现状关键结论（决定增量）

1. 真高分重渲染（setPixelRatio + 有界 idle）已在 master（#527 修复）→ P1 增量 = 超时**降级**（现=失败）、引擎级单飞、栅格底图 oversample 接线。
2. PDF 仍栅格壳 + CJK 文本层结构性缺失（jsPDF WinAnsi）→ P3 需随仓 OFL CJK 子集字体。
3. vector-pdf 后端端点已存在但零前端调用（#1213 死接口）→ P2 接线 = 收口该 issue。
4. 07 线未合入（分支无独有提交）→ **版面描述中间层由本线定义**，PR 置顶贴出供 07 消费。

## 阶段执行单

| 阶段 | 交付 | 状态 |
|---|---|---|
| P0 | recon 文档 + DPI 基线（探针脚本 `frontend/scripts/ac08/dpi-line-probe.mjs`） | ✅ |
| P1 | `frontend/lib/export/highdpi.ts`：降级/单飞/栅格披露 + 词表 + 探针 | ✅ 4fd8fb3c |
| P2 | 版面描述 IR + vector-svg IR 消费 + exporter 接线 + golden 对拍 | ✅ ff8b8883/531f2ded |
| P3 | pdf-font + vendored Noto Sans SC + 真文本层 + pypdf 门禁（样例入档） | ✅ ff8b8883 |
| P4 | extent 数学（修单位 bug）+ prepareWysiwygCamera + 超界/超时披露 | ✅ ff8b8883 |
| P5 | color_mode=cmyk：PDF 出血页 + 裁切线、SVG 标记、栅格近似披露 | ✅ ff8b8883 |
| P6 | layout_description.py 镜像 + golden 对拍 + pdf_renderer 整饰 + report 同源 | ✅ 531f2ded |
| P7 | 前端 389 绿 + pypdf 提取 4 绿 + 死码门 67 绿 + ruff/eslint/build 净 | ✅ |
| P8 | next build 一次通过 + CHANGELOG + ADR-0157 + 决策日志 + 样例 README | ✅ |

## 资源纪律遵守点

- 导出实测：单页、串行、小尺寸（720×480）、无并发（探针已按此实现）。
- `next build` 仅 P8 一次；vitest 日常跑 map-kit + 新增 export 目录。
- 后端 pytest 仅 `tests/unit/test_pdf*` / `tests/cartography/` 相关 scope。
- WeasyPrint 本机无 pango（Windows 已知降级面）：P6 对拍不依赖 WeasyPrint 渲染，走 503/degraded 语义测试。

## 决策日志（增量）

- 2026-09-13 `pip install -e .` 不可行（flat-layout 多顶层包拒建；兄弟 worktree 同）→ requirements 安装 + `pytest.ini pythonpath=.`。已记入 recon §0.2。
- 2026-09-13 P4「出图范围预览」以**数据 + 诊断词**形态交付（可视化预览组件属 `components/map/**` = 07 线禁改区），layout description 暴露 exportExtent/maskExtent/overflow 供 07 消费。

## 复现命令（门禁）

```bash
# 前端单测（日常门禁）
pnpm --dir frontend exec vitest run lib/map-kit lib/export --reporter=dot   # 389 passed
# 类型检查 / 构建（P8 唯一一次）
pnpm --dir frontend exec tsc --noEmit && pnpm --dir frontend exec next build
# DPI 线宽基线（真重渲染 vs 放大插值）
cd frontend && node scripts/ac08/dpi-line-probe.mjs --dpi 96,150,300 --out ../docs/dev/ac-08-samples/dpi-baseline
# PDF 中文文本层样例（pypdf 门禁消费）
cd frontend && node scripts/ac08/pdf-text-probe.mjs --out ../docs/dev/ac-08-samples
# 后端 scope 测试
./.venv/Scripts/python -m pytest -q -p no:cacheprovider tests/cartography/test_layout_description_golden.py   tests/cartography/test_export_sample_pdf_text.py tests/cartography/test_diagnostics_dead_code_gate.py   tests/unit/test_report_layout_parity.py tests/unit/test_pdf_renderer.py
```

## 交付台账（任务 → 文件 → 测试 → 证据）

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| P0 勘察 | docs/dev/ac-08-export-recon.md；frontend/scripts/ac08/dpi-line-probe.mjs、tilezoom-dpr-probe.mjs | —（只读 + 探针） | §6 基线表 + docs/dev/ac-08-samples/dpi-baseline/ |
| P1 降级 | frontend/lib/export/highdpi.ts；exporter.ts（单飞/接线）；export-chrome.ts 词表 | highdpi.test.ts（10）；exporter.singleflight.test.ts | 300DPI 超时降级路径有测试（§5-6） |
| P2 中间层 | frontend/lib/export/layout-description.ts；vector-svg-export.ts（IR 消费）；exporter.ts 装配 | layout-description.test.ts；vector-svg-ir-consumer.test.ts；layout-description.golden.test.ts | SVG-PNG 同源探针（§5-4） |
| P3 文本层 | frontend/lib/export/pdf-font.ts；exporter.ts exportToPDF；public/fonts/ | pdf-font.test.ts；exporter.pdftext.test.ts；tests/cartography/test_export_sample_pdf_text.py（pypdf 4） | sample-export-cjk.pdf（Type0+FontFile2，中文可提取） |
| P4 WYSIWYG | frontend/lib/export/extent.ts、spec-bounds.ts；exporter.ts prepareWysiwygCamera | extent.test.ts；exporter.wysiwyg.test.ts | fitBounds 接线 + 解析投影 ≤1px 基准（§5-3） |
| P5 出版档 | exportToPDF 出血/裁切线；svg-marginalia.renderSvgCropMarks | exporter.pdftext.test.ts 出版档段 | 裁切线角点坐标断言 |
| P6 后端对拍 | app/lib/cartography/layout_description.py、pdf_renderer.py、render_diagnostics.py（+6 码）、report_service.py | test_layout_description_golden.py；test_report_layout_parity.py；test_pdf_renderer.py；test_diagnostics_dead_code_gate.py | golden corpus 双端对拍 1e-9（§5-7） |
| P7/P8 门禁 | — | 前端 390 绿；后端 scope 90 绿 1 skip；tsc 0；build exit 0；eslint/ruff 净 | 本表 + PR 门禁段原文 |
| Review | 46a7ed21 | 回归：heatmap→mixed / 长标签→vector | S2 Spec 审查 PASS-WITH-FINDINGS → finding 已修 |

## Review 取证（§6）

1. `git diff origin/master --stat`：54 files，+4486/−165（含两份字体资产与样例）。
2. code-review 双轴：Standards 轴主 agent 自审（含 atlas+cmyk addPage 尺寸漂移修复）；
   Spec 轴 S2 独立 subagent → PASS-WITH-FINDINGS，唯一实质 finding（mixed 误判）
   已修（46a7ed21），其余为已登记的文档化取舍（D5/D9）。
3. §5 全量重跑取证：见上方「复现命令」与本表测试列（2026-09-13 原文）。
