# Cartography V6 — Plan（Phase C 执行序）

依审计与 R1 修订重排；每 wave：实现→targeted tests→lint/typecheck→progress→独立 commit。

| Wave | 内容 | 关键产物 | 状态 |
|---|---|---|---|
| W1 | 死码门发射器注册表 + 诊断保留槽策略 | render_diagnostics.py EMITTER_REGISTRY + 契约测试 | done |
| W2 | authoritative MapSpec schema（strict Pydantic、迁移、canonical dict 拷贝、脏值 corpus） | app/lib/cartography/mapspec_schema.py + tests | done |
| W3 | TS 投影生成器（核心文档类型）+ types.ts re-export + idempotence/契约测试 | scripts/generate_mapspec_ts.py + types.generated.ts | done |
| W4 | 后端 canonical render scene + 共享 golden fixtures 双语言 parity | app/lib/cartography/render_scene.py + corpus | done |
| W5 | legend 单源（前后端 derive 模型 + 六点 diff 表 + 闸更新） | legend-model.ts + derive_legend + tests | done |
| W6 | label collision 导出接线（后端 opt-in + TS 移植 + 差分 fixtures + 诊断） | twin 接线 + label-solver.ts | done |
| W7 | 后端 publication chrome（svg_marginalia.py + compile_publication_svg + report_service 升级） | publication SVG + goldens | done |
| W8 | vector PDF 端点（WeasyPrint 串行、字体探测、raster/tile 解析、SSRF-deny、诊断） | publication_export.py + route | done |
| W9 | spec 级 frames（layout.frames + 后端多页 + 前端 spec-frames 适配） | schema/编译/frame-composer 接线 | done |
| W10 | 高级制图 native 收口（graticule/neatline/scale 纬度修正/inset） | svg_marginalia 扩展 | done |
| W11 | perf 结构断言 + a11y（svg title/desc）+ ADR-0120/docs/catalog/CHANGELOG | 收尾 | done |

## 测试矩阵（04-test-matrix.md 维护精确数字）

后端：schema/迁移/脏值 corpus、render scene parity、publication chrome golden、label 差分、
vector pdf（weasyprint-gated）、frames/atlas、诊断契约+死码门、既有回归（cartography 33 文件 + twin parity + report_service）。
前端：vitest legend-model、label-solver 差分、render-scene fixtures parity、types generated 契约、
既有 map-kit 26 测试回归、tsc/eslint。
