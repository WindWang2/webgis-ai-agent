# F14 — Publication / Export Parity：架构设计（ADR-0211 增补）

状态：design → 实现中（2026-09-26）。本文是 ADR-0211 的 follow-up 设计，
不推翻其任何决策；全部变更 additive，旧行为缺省不变。

## D1 单一 support/parity matrix（component ABI 驱动）

**决策**：`component_renderers.ComponentRendererSupport` 增加 `publication: bool`
字段（publication 矢量链是否真渲染该族）；`PUBLICATION_COMPONENT_TYPES` 从
「mapspec_to_svg 手工 frozenset」改为**由矩阵派生**（`component_renderers`
导出派生值，`mapspec_to_svg` re-export 保持既有 import 面与
product_completeness 消费不变）。

- 为什么放矩阵：`_SUPPORT_MATRIX` 已是 live/canvas 支持的唯一权威，
  `ComponentRegistry.validate()` 交叉对账 descriptor；publication 通道并入后
  「矩阵声称 SVG 导出但后端 publication 链不渲染」的脱节从结构上消灭。
- 新增规则（测试锁定）：`publication=True` 的类型，其 SVG 渲染分支必须真实存在
  —— 由 golden corpus 的矩阵一致性用例（逐类型编译→断言 chrome marker 在位）
  与 unified completeness 对账用例共同锁定。
- canvas/exporters 语义不动（png/pdf/svg 指 canvas 导出链）。

## D2 publication 矢量链补齐 8 族（WP2）

新片段生成器进 `app/lib/cartography/svg_marginalia.py`（纯函数、canvas 坐标系、
确定性输出、用户文本全转义 —— 与既有 8 个片段同纪律）：

| 族 | 类名（corpus 契约） | 数据 | 有界性 |
|---|---|---|---|
| continuous_colorbar | `chrome-colorbar` | layer.legend_spec（min/max/palette_colors/unit/nodata） | 色阶 ≤24 stops |
| annotation | `chrome-annotation` | options.text/items/anchorCoordinate | ≤8 行/条 |
| statistics_panel | `chrome-panel` + `data-kind="statistics"` | options.stats | rows ≤12 |
| chart_panel | `chrome-panel` + `data-kind="chart"` | options.chart（chartRef 水合后） | points ≤64/series，series ≤6 |
| table_panel | `chrome-panel` + `data-kind="table"` | options.table / tableRef 水合 | ≤8 行 ≤6 列 + 尾注 |
| methodology/uncertainty/decision | `chrome-panel` + `data-kind=…` | options.warnings/uncertainty/decision | rows ≤16 |

- 图表 kind 支持面与 `chart_kinds.CHART_KINDS` 对账：`export_level != "unsupported"`
  的 kind 给出忠实/近似矢量绘制（axis 族/polar 族/matrix/kpi/ranking），近似绘制
  随面板披露（既有码 `chart_kind_unsupported_export` 只给 violin/未知 kind）。
- 装配在 `mapspec_to_svg._render_chrome_groups`：anchor 槽位布局（复用
  ResolvedComponent.anchor），多面板同锚位按栈内偏移；`disabled/缺席 → 不画`
  （user-wins 不变）；无数据 → 面板缺席 + 结构化降级（不画空卡冒充）。
- 版面截断（legend >12、面板行溢出、注记行溢出）新词表码
  `publication_layout_truncated`（warning，detail= family+counts），EMITTER_REGISTRY
  登记 mapspec_to_svg —— 死码门继续成立。

## D3 组件覆盖回执（structured degradation receipt，WP6）

`SvgCompilation` 增 additive 字段：`rendered_component_types: List[str]`（去重排序，
≤24）与 `omitted_components: List[Dict[str,str]]`（`{component_id, type, code}`，
≤16）。来源：chrome 装配实况（渲染了什么、为什么省略 —— ref 不可用/kind 不支持/
配置无效/矩阵 publication=False 的 catch-all 新码 `publication_component_omitted`）。

- `render_publication_pdf` 逐帧聚合 → `PublicationPdfResult.component_coverage`。
- 矢量路由把 coverage + diagnostics 写成与 canvas 链同形的
  `{filename}.diagnostics.json` sidecar（GET /export/diagnostics 立即可读，
  所有权同源）。
- `ExportLineageInfo` 增 `degradation_codes: Optional[List[str]]` 与
  `component_coverage: Optional[Dict]`（exclude_none：缺席=未记录）。
- `record_export_lineage` 增对应入参 → registry metadata 有界入档（WP8）。

## D4 finalizer 幂等门第四键（WP3）

`workflow_instance.product_state_fingerprint(chapter)`：对
`export_receipts`（全量、含 format/revision/filename/created_at）与
`product_spec.digest`（存在时）做 canonical-JSON sha256（与 rows_fingerprint
同规、[:2048] 同宽）。

- `_dedup_gate_blocks` 合取式加第四键；`map_product_block` 持久化
  `product_state_fingerprint`；持久化点在**锁内**从 fresh 章节计算（receipts
  mid-run 漂移自然被行漂移守卫同款逻辑覆盖 —— 键在锁内取自最终章节）。
- 方向安全：键只会**打开**门（多重验），不会把未验证状态挡在门外；旧块无键 →
  一次性重验自愈补齐（rows_fingerprint V2 同款兼容语义，已在 docstring 披露）。
- 效果：READY 后新 export receipt / semantic-only 产品编辑（改 digest）→ 下一个
  finalizer 触发点重验 → completeness/goal_satisfaction 用新证据裁决
  （绝不假 PASS 的失败方向保守原则不变）。

## D5 EXPORT_DIR 单一真相（WP4）

新模块 `app/services/export_paths.py`：`exports_root() -> Path`
（= `Path(settings.DATA_DIR)/"exports"`，**调用时**取值）与
`ensure_exports_root()`（幂等 mkdir）。消费方收口：

- `map.py`：删除 import 期常量与 makedirs；全部 11 处引用改走调用时函数；
  测试 patch 点从 `patch.object(map_mod, "EXPORT_DIR", …)` 迁移到
  `monkeypatch.setattr(export_paths, "exports_root", lambda: Path(...))`（约 18 处，
  机械替换）。
- `artifact_lifecycle.py`：`_sweep_exports` 改调用时 root；模块常量 `EXPORT_DIR`
  移除（无测试消费）。
- `artifact_registry.export_file_path`：委托 `exports_root()`（仍是 ref→path
  的唯一解析器；export_paths 只管 root）。
- GC 对用户交付物安全：orphan 回收不 unlink export 文件（#1483 已钉）；age-based
  sweep 增加生成器前缀护栏（只回收 `map_export_*` / `map_vector_*` /
  `map_report_*` 已知前缀 + `.owner`/`.diagnostics.json` 边车），操作员放置的
  其它文件绝不删 —— 交付物目录的防御性收窄，保留期语义（7d 默认，
  EXPORT_RETENTION_DAYS 覆盖）不变。

## D6 出版基础（WP5）

- **DPI**：canvas /export 增可选 Form `dpi`（前端 exporter.ts uploadExport 携带
  既有 dpi 变量；72–600 钳制后入 lineage metadata.dpi）—— 此前 canvas 链
  lineage 永远缺 dpi。
- **CJK 字体内嵌**：`_probe_cjk_font()` 为假且 vendored
  `NotoSansSC-Regular-subset.ttf` 可读时，页面 HTML 注入 `@font-face`
  data-URI（进程内缓存 base64，约 3.1MB 一次性）+ 发射既有码
  `pdf_cjk_font_embedded`（EMITTER_REGISTRY 增 publication_export 发射点）；
  失败回退现状（`pdf_font_fallback` 照旧）。SVG 片段的字体族不改
  （byte-stable 契约），由页面 CSS `svg text { font-family: … }` 统一。
- **page profile**：`FramePageSize` 增可选 `profile`（`a4/a3/a2/a1/a0` ×
  `portrait/landscape` 词表），`_frame_geometry` 解析优先级 profile > 裸宽高 >
  A4 landscape 缺省；非法值诚实落回缺省并披露（schema disclosure 既有通道）。
- **legend overflow**：图例 >12 条截断 + `publication_layout_truncated`（D2）。

## D7 live vs export 语义 golden corpus（WP7）

新模块 `app/lib/cartography/export_semantic_corpus.py`（纯函数、无 I/O）：

- `expected_semantics(mapspec) -> dict`：spec → 应然语义面（chrome 族集合、
  legend 条目数、colorbar stops、chart series/points、table 行列、标题/注记文本、
  隐藏图层集合）。
- `extract_semantics(svg) -> dict`：defusedxml 解析导出 SVG → 实然语义面
  （chrome marker 在位性 + 计数 + 关键文本）。
- `compare(expected, actual, publication_types) -> ParityReport`：逐族裁决
  `match / degraded(有 receipt) / missing(静默丢失=fail)`；隐藏图层在导出件
  出现 = fail。报告 bounded、可序列化、稳定 reason codes。
- 语料：`tests/fixtures/export_semantic_corpus/case_*.json`（每 case =
  mapspec + expected 摘要），测试驱动「编译→抽取→比较」闭环；矩阵
  `publication=True` 一致性用例（D1）也挂在此测试文件。

## D8 兼容性 / 回滚

全部 additive：矩阵新字段有缺省、frozenset 内容等值替换（本 PR 内先扩后锁）、
SvgCompilation/Result/lineage/schema 新键全 optional、门第四键只增重验、
测试 patch 点同 PR 内迁移。revert 单 commit 序列即回滚。

## Out of scope

- atlas_layout.py 生产接线（休眠模块，需 schema 帧派生决策 —— 独立工作包）；
- 前端矢量入口接线（#1213 已披露 backend-only）；
- i18n #1436、真实浏览器截图比对（corpus 语义面已覆盖结构 parity）。
