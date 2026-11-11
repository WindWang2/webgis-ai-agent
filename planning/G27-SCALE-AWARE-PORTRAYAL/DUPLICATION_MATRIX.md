# G27 DUPLICATION MATRIX — 能力 / 证据 / 剩余增量

> 规则：已合并能力只回归、不重建；只有"真实调用图"能判定缺口，文档声称不算。

| 能力 | 现状证据（文件:行 / 命令） | Owner / PR / branch | G27 剩余增量 | 共享接口（复用，不新建第二套） | 明确不做 |
|---|---|---|---|---|---|
| 显隐 + 结构 zoom 门单一 precedence | `frontend/lib/carto-ir/visibility.ts:12-35,88-175`；生产者 runtime.ts:1252/1836/1934、compiler.ts:642、mapspec-to-svg.ts:598/756/1411；`visibility.test.ts` | C11 / ADR-0218（已合并） | 只回归；新解析结果**调用** `resolveLayerVisibility` 的结果语义，不复制规则 | `resolveLayerVisibility` / `zoom_gate_of_layer` | 不重写 precedence、不新增第二 resolver |
| Representation-at-scale 窗口计划 | `app/lib/cartography/representation.py:310-482,534-616`；落笔 `app/services/analysis_cartography_converter.py:883-931`；前端 `carto-ir/representation.ts` | 方向5 / v1.6 / PR #1880 声明"复用不重写" | 把该决策的 LOD 分量投影到**既有** `label.zoomBands` / `thresholds.maxFeatures`（opt-in），使纯函数表真正进 runtime | `representation_at_scale` / `project_mapspec_representation` | 不改窗口计划语义、不动 user-wins |
| 语义尺度带 / 标注 zoomBands / hysteresis | `scale_rules.py:54-105,115-161`；`label_plan.DEFAULT_ZOOM_BANDS`；`test_scale_reconciliation_v1.py:32-37` 锁四表差异 | ADR-0205 / ADR-0154 | 复用 `resolve_scale_tier_stable` 做去抖分档（首个真实运行时消费者）；档位边界由**同一 corpus** 锁 backend/frontend | `SCALE_TIERS` / `resolve_scale_tier_stable` | 不合并 0/8/11/14/24 与 3/6/10/14/19 两张表 |
| scene_lod 四档 LOD 表 | `scene_lod.py:20-99`；`lod_for_zoom` 唯一生产调用 representation.py:452 | ADR-0199 M5 | **补齐 3 个真实零消费者**：`build_label_zoom_bands` / `terrain_maxzoom_for_zoom` / `decimate_budget`；产物落既有 spec 字段与解释面 | `LOD_BANDS` / `lod_for_zoom` | 不删表、不改四档数值、不扩展 3D terrain |
| ScaleRepresentationProfile.v1（含 CAS/receipt/工具） | `multiscale/profile.py`、`gis_harness/scale_profile/service.py:526-599,613-680`、`app/tools/scale_profile_tools.py`、schema v1.9 | **PR #1880**（已合并，18 commits） | 零重建；仅在新策略工具里复用 `expected_revision`/CAS/undo 模式 | `apply_gis_mutation` / `expected_revision` / undo 栈 | 不新建第二套 profile、不动 Schema 版本号 |
| 逐要素标注 / 要素级锁 / pin | `mapspec_schema.py` v2.1、`frontend/lib/mapspec-runtime/label-layout.ts:45-106,176-214,272`、runtime.ts:1811 | **PR #2077 G16** | 复用 `layer.label.zoomBands` 与既有 pin/锁优先级；不新建标注状态 | `normalizeLabelStrategy` / `layer.label` | 不重做 G16 |
| 多画幅 / frame extent | `publication_ir.py:195-274,643-733`、PR #2076 G15 | **PR #2076** | P5 只加**加性可选** scale viewport 输入；frame 各自等价 viewport | `page_geometry` / `resolve_effective_bounds` / `FrameSlotIR` | 不重做 G15 frame |
| 栅格表达 / 热力图 px vs 米 | `heatmap_contract.py`、`resolveHeatmapRadiusPx`、`scale_rules.meters_per_pixel`、PR #2078 G20 | **PR #2078** | 解释面**披露** radius_px（屏幕 px）≠ bandwidth_m（真实距离），不做跨 zoom 同量纲伪装 | `radius_px` / `bandwidth_m` / `meters_per_pixel` | 不改 G20 renderer、不新增未经支持的 variant |
| Live ↔ Export parity | `docs/cartography/RENDER_EXPORT_PARITY.md:9-38`、`export_adjudicate_layer`、`mapspec_to_svg.py:1193/1307/2065` | G04 / ADR-0165 | 只改 **scale resolver seam**：DPI 显式只影响采样/尺寸；不同 CRS/纬度不套固定公式 | `export_adjudicate_layer` / `export_zoom_of_mapspec` | 不改 G26 raster renderer、不把 DPI 当语义 zoom |
| 组件 registry / composer / 模板匹配 | `component_registry.py:45-100,294-299`、`component_composer.py`、`template_intelligence.py`（zoom/scale/tier 命中 0） | C14 / G04 | 组件读取**同一解析结果**的档位状态；仅使用 registry 已声明的真实 variant，不加新 variant | `resolveMapComponents` / `composeMapLayout` | 不新增未经 renderer 支持的 variant、不重做模板库大批次 |
| CartoIR 契约词表 + 共享 corpus | `tests/cartography/carto_ir_corpus/contract_manifest.json`、`frontend/lib/carto-ir/contract-manifest.ts`、`test_carto_ir_corpus_v1.py` | C11 | **加性**扩充 scale 语义案例（新 fixture，不改现有 manifest 断言），backend pytest 与 frontend vitest 消费同一案例 | `contract_manifest.json` / `contract-manifest.ts` | 不批量刷新 golden、不手改生成文件 |
| Pi 原生工具发现 / 嵌套调用 / 配置换代 | `pi_native_surface.py`、PR #2081（修 #2061） | **PR #2081** | 新工具走既有 ToolRegistry + `webgis_execute` 长尾代理，复用真实权限/tier/CAS | `ToolRegistry` / `webgis_execute` / `pi_native_surface` | **不升级 Pi vendor/gitlink/compat matrix（G22 独占）**；不注册 `webgis_execute` |
| G09 批量分析 / G21 OD flow / 模板算法大批次 | 远端分支 `origin/zcode/goal-g09-reusable-workflow-batches-*`、`origin/zcode/goal-g21-directed-od-flow-*`；ledger `planning/goal-multiscale-carto/*` | 其他方向在途 | 不立项、不重建 | — | 明确不做 |
| R1 审查项 / 开放 PR #2084-2086 | `gh issue list` #2037–#2072；`gh pr list --state open` | 他人 owner | 仅登记依赖/回归边界 | — | 明确不做；不把三个修复计为 G27 工作量 |

## 判据说明

- 「文档声称 ≠ 生产」：`docs/dev/semantic-zoom-representation-runtime.md:12` 称"scene LOD 零生产接线"，但 `representation.py:452` + `converter.py:915` 是真实调用；该文 `:68` 自述未接线的是 3D runtime。
- 「四表并存 ≠ bug」：`test_scale_reconciliation_v1.py:32-37` 与 `test_representation_protocol_v1.py:68-75` 显式锁定 0/8/11/14/24 与 3/6/10/14/19 的差异为有意设计。
- 「DPI ≠ 语义 zoom」：`publication_export.py:1101-1107` 的 `dpi_scale` 只缩放几何/字号；`_compute_oversample_boost` 只作用于瓦片采样 z；表达裁决 zoom 恒取 `spec.view.zoom`。
- ADR 编号：`docs/adr` 最新 `0289`；`docs/adr/G20-RASTER-EXPRESSION.md:3-4` 明确并行方向不得抢占同一四位编号 → 本方向使用**方向命名**文件 `docs/adr/G27-SCALE-AWARE-PORTRAYAL.md`，合并时再按仓库规约定号。
