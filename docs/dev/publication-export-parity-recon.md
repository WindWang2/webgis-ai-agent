# F14 — Publication / Export Parity Foundation：Recon（2026-09-26）

基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-23 merge #1494）。
分支：`zcode/f14-publication-export-parity-20260926-9e1ad229`，worktree
`../wt-webgis-f14-publication-export-parity-20260926-9e1ad229`，merge-base = 9e1ad229。
注意：主仓本地 `master`（d5315716，16 个 audit commits）**不含** #1483，与 origin/master
在 184d4917 分叉 —— 本方向全部工作只基于 origin/master worktree，禁止从本地 master 搬运。

## 1. GitHub 状态（执行时实测）

- Open PR：仅 **#1489**（dependabot docker node bump，只改 Dockerfile*）——零文件重叠。
- 最近功能性波次：**#1479–#1488**（2026-09-21 合入）；#1489–#1496 全部为 dependabot。
- 直接前置：**#1483**（ADR-0211，export lineage + unified completeness），其 PR 描述
  Follow-up 明确列出本方向的四个缺口（幂等门打破键 / EXPORT_DIR 双轨 /
  publication 链缺 panel/colorbar/annotation / lineage 强化）。
- Open issues：#1436（i18n）、#1377（audit 延期跟踪）——与本方向零交集。
- 并行 worktree：f08/f09/f11/f12/f13/f15 六路（避免热区：pipeline.py / exporter.ts /
  artifact_registry.py 的改动保持最小、新逻辑一律进新模块）。

## 2. Already Done（禁止重复实现）

| 能力 | 位置（origin/master） |
|---|---|
| export lineage + receipt（ref:export cursor、export_receipts 首个生产方） | `app/services/export_lineage.py`；`tests/unit/test_export_lineage.py` |
| 统一 completeness + delivery 披露（publication 真值查询） | `app/services/gis_harness/product_completeness.py:50-60,244-259` |
| 豁免单源 `EXPORT_PARITY_EXEMPT_TYPES` | `app/lib/cartography/component_renderers.py:33` |
| `PUBLICATION_COMPONENT_TYPES` 真值导出（10 族 chrome） | `app/services/mapspec_to_svg.py:430-433` |
| canvas 导出诊断 sidecar（/export 路由） | `app/api/routes/map.py:242-281` |
| 降级码权威词表 + 死码门（EMITTER_REGISTRY） | `app/lib/cartography/render_diagnostics.py` |
| golden_diff **像素**工具 + 基线脚本 | `app/lib/cartography/golden_diff.py`、`scripts/golden_baseline.py` |
| compiler parity / cartographic regression 结构测试 | `tests/unit/test_compiler_parity.py`、`tests/quality/test_cartographic_regression.py` |
| render observation（live 侧结构化真值、服务端盖章代次） | `app/services/gis_harness/render_observation.py` |

## 3. Still Missing（= 本方向工作包）

1. **publication 矢量链缺 8 个组件族**：`continuous_colorbar / annotation /
   statistics_panel / chart_panel / table_panel / methodology_note /
   uncertainty_panel / decision_panel` 在 mapspec_to_svg 0 命中（grep 证据），
   而 `_SUPPORT_MATRIX` 对这些族的 exporters 已声明 png/pdf/svg（canvas 链有消费方）
   —— **矩阵声称的 SVG 导出能力与 publication 后端真值脱节**。
2. **finalizer 幂等门无 product/export 侧键**：`_dedup_gate_blocks`
   （completion/pipeline.py:582-615）三把钥匙 = checked_revision / render_obs_seq /
   rows_fingerprint（只覆盖 data_requirements+analysis_steps 行表，
   workflow_instance.py:167-176）。READY 后落新 export receipt、或 semantic-only
   product_spec 编辑（不推 revision）→ 门永不打开，goal_satisfaction 的
   export 评估在 READY 会话不可达。
3. **EXPORT_DIR 三轨**：map.py:38（import 时 str）/ artifact_lifecycle.py:43
   （import 时 Path）/ artifact_registry.export_file_path:344-351（调用时）。
   DATA_DIR 运行时变更后 probe 与写盘/GC 指向不同目录。
4. **矢量链无 diagnostics sidecar**：canvas /export 写 sidecar，/export/vector-pdf
   不写（grep 0）；`ExportLineageInfo` 不回带 degradation_codes（schema map_schema.py:64-70）。
5. **无 live↔export 结构语义 corpus**：golden_diff 是像素工具；结构 parity 测试
   分散且不构成可扩展语料。
6. **lineage metadata 无组件覆盖面**：只有 degradation_codes 摘要，无
   rendered/omitted component families（结构化降级回执的前半）。
7. 出版基础缺口：canvas /export 不传 dpi（exporter.ts 有 dpi 变量但 Form 不带）；
   publication 链无字体嵌入保证（仅 CSS 栈 + probe 披露）；legend >12 条静默截断；
   pageSize 无纸型 profile（仅裸 mm）。

## 4. Overlap / Must Not Touch

- pipeline.py（33 commits/45 天，God module）：只动 `_dedup_gate_blocks` 合取式、
  `map_product_block` 持久化键、调用点传参 —— 新指纹函数进 workflow_instance.py。
- exporter.ts / artifact_registry.py / map.py 热区：机械最小 diff（DPI 字段、
  路径函数替换、sidecar 写入、lineage 传参）。
- #1489（docker）：零交集。并行 worktree f08/f09/f11/f12/f13/f15：本分支新模块
  为主，接触共享文件限于上述机械点。

## 5. Integration Seams（接线点）

- 矩阵：`component_renderers.ComponentRendererSupport` 增 `publication: bool`；
  `PUBLICATION_COMPONENT_TYPES` 改为**派生**（mapspec_to_svg re-export 保兼容）。
- publication 渲染：`svg_marginalia.py` 新片段生成器（colorbar/annotation/panel 族）
  + `mapspec_to_svg._render_chrome_groups` 装配 + `SvgCompilation` 增覆盖回执字段。
- finalizer 门：`workflow_instance.product_state_fingerprint`（receipts+spec digest
  canonical fingerprint）→ 门第四键。
- 路径：新 `app/services/export_paths.py::exports_root()`（调用时取值）；
  map.py / artifact_lifecycle / artifact_registry 全部委托；测试 patch 点迁移。
- sidecar/lineage：矢量路由复用 canvas sidecar 通道；`ExportLineageInfo` 增
  `degradation_codes` / `component_coverage`（exclude_none 语义不变）。
- corpus：新 `app/lib/cartography/export_semantic_corpus.py`（结构语义 profile/
  extract/compare 纯函数）+ `tests/fixtures/export_semantic_corpus/*.json` 语料。

## 6. 组件 options 数据协议（前端 export-chrome.ts 镜像目标）

| 族 | options 键 | 形状 |
|---|---|---|
| statistics_panel | `stats` | `{title?, items:[{label?,value?,unit?}]}` |
| chart_panel | `chart` / `chartRef` | `{type(18 kind 词表), title, data:[{name,value?,x?,y?,q1..max}], series?, stacked?, x_label?, y_label?}`；ref 需会话水合 |
| table_panel | `table` / `tableRef` / `layerId` + `title`/`columns` | 有界快照 ≤8 行 ≤6 列 + 总量尾注 |
| methodology_note | `warnings` | `[{code, text}]` → "CODE text"，accent |
| uncertainty_panel | `uncertainty` | `{items:[{label,kind,detail}], sampleNote}` |
| decision_panel | `decision` | `{method?, weightSource?, rows:[{name,rank?,score?,basis?}], vetoes?}`，basis=vetoed 删除线 |
| annotation | `text`（多行）/ `items`（组）/ `anchorCoordinate`（callout） | ≤8 行 |
| continuous_colorbar | layer.legend_spec | `{type:continuous, palette_colors[], min, max, unit?, field?, nodata?}`，变体 vertical/stepped/scientific |

`chartRef`/`tableRef` 的会话水合在 publication 链对齐 `hydrate_ref_sources_sync`
模式（best-effort per ref；不可用 → 既有码 `chart_ref_unavailable` /
`table_ref_unavailable` + 组件级省略回执，不伪造空面板）。
