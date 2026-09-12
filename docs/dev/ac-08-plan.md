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
| P1 | `frontend/lib/export/highdpi.ts`：降级/单飞/oversample + exporter 接线 + 测试 | ⏳ |
| P2 | `frontend/lib/export/layout-description.ts`（同源中间层）+ composeLayout/vector-svg 消费 + parity 测试 | ⏳ |
| P3 | `frontend/lib/export/pdf-font.ts` + CJK 子集字体 + PDF 真文本层 + pypdf 提取断言 | ⏳ |
| P4 | `frontend/lib/export/extent.ts`（WYSIWYG 数学）+ 接线 + 超界提示诊断 | ⏳ |
| P5 | 出版档 `color_mode` + 出血 3mm + 裁切标记 | ⏳ |
| P6 | `app/lib/cartography/layout_description.py` 镜像 + golden fixtures + pdf_renderer 整饰 + report 对拍 | ⏳ |
| P7 | §5 门禁全绿（探针重跑取证） | ⏳ |
| P8 | 唯一一次 `next build` + typecheck + CHANGELOG + ADR-0157 定稿 + 样例 | ⏳ |

## 资源纪律遵守点

- 导出实测：单页、串行、小尺寸（720×480）、无并发（探针已按此实现）。
- `next build` 仅 P8 一次；vitest 日常跑 map-kit + 新增 export 目录。
- 后端 pytest 仅 `tests/unit/test_pdf*` / `tests/cartography/` 相关 scope。
- WeasyPrint 本机无 pango（Windows 已知降级面）：P6 对拍不依赖 WeasyPrint 渲染，走 503/degraded 语义测试。

## 决策日志（增量）

- 2026-09-13 `pip install -e .` 不可行（flat-layout 多顶层包拒建；兄弟 worktree 同）→ requirements 安装 + `pytest.ini pythonpath=.`。已记入 recon §0.2。
- 2026-09-13 P4「出图范围预览」以**数据 + 诊断词**形态交付（可视化预览组件属 `components/map/**` = 07 线禁改区），layout description 暴露 exportExtent/maskExtent/overflow 供 07 消费。
