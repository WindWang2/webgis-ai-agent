# F14 — Publication / Export Parity — Design（最小 design 摘要）

- Date: 2026-09-26（实现） / 2026-09-27（本文为 f14 命名空间锚定文档，G06 文档债补齐）
- 背景: ADR-0211（`docs/adr/0211-product-export-lineage-unified-completeness.md`）的 follow-up，#1483 PR 描述列出的四个缺口（幂等门缺 product/export 侧键 / EXPORT_DIR 多轨 / publication 矢量链缺组件族 / lineage 强化）
- 完整三件套（本方向主文档，无 f14- 前缀）: `docs/dev/publication-export-parity-design.md`（D1–D8 全量设计）、`docs/dev/publication-export-parity-recon.md`、`docs/dev/publication-export-parity-review.md`
- 实现基线: `origin/master @ 9e1ad229`；Merge: PR #1504（`47be826a`）
- 状态: 已合并（merged）。本文为 design 摘要，全部内容以合并后代码为事实源，可 rg 核对。

## 实现摘要（8 个工作包，全部 additive、缺省行为不变）

| WP | 交付 | 代码锚点 |
|---|---|---|
| WP1 支持矩阵单源 | `ComponentRendererSupport` 增 `publication: bool`（`app/lib/cartography/component_renderers.py:46`）；`PUBLICATION_COMPONENT_TYPES` 改由 `_SUPPORT_MATRIX` 派生（:205），`mapspec_to_svg` re-export 保既有 import 面 —— 「矩阵声称 SVG 导出但 publication 链不渲染」的脱节从结构上消灭 | component_renderers.py |
| WP2 矢量链补 8 族 | 片段生成器进 `app/lib/cartography/svg_marginalia.py`（纯函数、canvas 坐标系、确定性、文本全转义）：`chrome-colorbar` / `chrome-annotation` / `chrome-panel[data-kind=…]`（:309/:364/:369）；图表绘制新 `app/lib/cartography/svg_charts.py`；装配在 `mapspec_to_svg._render_chrome_groups`（:697）；user-wins 不变（disabled/缺席不画），无数据面板缺席不画空卡 | svg_marginalia.py / svg_charts.py / mapspec_to_svg.py |
| WP3 finalizer 幂等门第四键 | `workflow_instance.product_state_fingerprint`（`app/services/gis_harness/workflow_instance.py:209`，export_receipts 全量 + product_spec.digest 的 canonical-json sha256）并入 `_dedup_gate_blocks` 合取式（`completion/pipeline.py:699`）；键只会打开门（多重验），旧块无键一次性重验自愈 | workflow_instance.py / pipeline.py |
| WP4 EXPORT_DIR 单一真相 | 新 `app/services/export_paths.py`：`exports_root()`（:25，调用时取值）/`ensure_exports_root()`（:30）；map.py / artifact_lifecycle / artifact_registry 全部收口委托；GC 前缀护栏 `_EXPORT_GENERATED_RE`（artifact_lifecycle.py:75，无 .owner 边车的文件仅匹配 `map_export_*`/`map_vector_*` 两个生成器前缀才可回收；`data/reports` 等交付物永不回收，操作员文件绝不删） | export_paths.py / artifact_lifecycle.py |
| WP5 出版基础 | canvas /export 增 `dpi` Form（前端 `exporter.ts` 携带，入 lineage metadata）；CJK 字体内嵌（`_probe_cjk_font` 为假时 vendored NotoSansSC data-URI `@font-face` + `pdf_cjk_font_embedded` 披露）；纸型 profile：schema `PageProfile` 词表（`mapspec_schema.py:432`）+ `FramePageSize.profile`（:444），解析优先级 profile > 裸宽高 > A4 landscape（`publication_export.PAGE_PROFILES` :59/:226） | map_schema / publication_export / exporter.ts |
| WP6 覆盖回执 + sidecar | `SvgCompilation` 增 `rendered_component_types`/`omitted_components`（mapspec_to_svg.py:155-156，有界 ≤24/≤16）；`render_publication_pdf` 逐帧聚合 → `PublicationPdfResult.component_coverage`（publication_export.py:95/:108）；矢量路由写与 canvas 链同形 `{filename}.diagnostics.json` sidecar（map.py:373/:413）；`ExportLineageInfo` 增 `degradation_codes`/`component_coverage`（`app/schemas/map_schema.py:74-75`），`record_export_lineage` 对应入参入档（export_lineage.py:97/:109-110） | mapspec_to_svg / publication_export / map.py / map_schema |
| WP7 live↔export 语义 corpus | 新 `app/lib/cartography/export_semantic_corpus.py`：`expected_semantics`（:182）/`extract_semantics`（:345，defusedxml）/`compare_semantics`（:516）→ `match / degraded / missing(=fail)` 逐族裁决，隐藏图层在导出件出现 = fail；语料 `tests/fixtures/export_semantic_corpus/case_*.json` | export_semantic_corpus.py |
| WP8 降级词表 | `publication_layout_truncated` / `publication_component_omitted` / `pdf_cjk_font_embedded` 进 `EMITTER_REGISTRY`（`render_diagnostics.py:282` 起）—— 死码门继续成立 | render_diagnostics.py |

## 验收对照（DoD）

- [x] 矩阵 publication=True 的族在矢量链真实渲染（golden corpus 矩阵一致性用例 + unified completeness 对账）
- [x] READY 后落新 export receipt / semantic-only 编辑 → 下一个 finalizer 触发点重验（第四键打开幂等门，`tests/unit/gis_harness/test_product_state_gate.py`）
- [x] DATA_DIR 运行时变更后 probe/写盘/GC 同源（`tests/unit/test_export_paths.py`）
- [x] 导出件结构语义可判错（`tests/unit/test_export_semantic_corpus.py`，512 行）
- [x] 矢量链诊断 sidecar 与 canvas 链同形可读（`tests/unit/test_vector_degradation_receipt.py` / `test_export_diagnostics_sidecar.py`）
- [x] DPI/字体/纸型/图例截断出版基础（`tests/unit/test_publication_basics_wp5.py`）
- [x] review 清偿（`tests/unit/test_review_fixes_f14.py`）

兼容性：全部 additive（矩阵新字段有缺省、frozenset 内容等值替换、新键全 optional、
门只增重验）；受控例外仅 map_border 输出新增 `chrome-map-border` 组包装（corpus
marker 契约的一部分，无消费方 pin 旧字节）。Out of scope：atlas_layout 生产接线、
前端矢量入口接线、真实浏览器截图比对（见主 design 文档 D8/Out of scope）。
