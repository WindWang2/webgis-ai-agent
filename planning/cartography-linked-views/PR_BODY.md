# feat(carto): scalable linked spatial views and label/export fidelity（Track B）

## 目标

让 Agent 产出的地图在真实大数据（50k–200k 要素）下可查询、可交互、可解释、可制图、可导出：MVT 大图层的服务端分页属性浏览与稳定要素定位、地图↔表格↔图表的真实联动、视口统计的科学口径、导出标注的专业质量收敛 —— 全部沿用既有 Pi + GIS Harness + MapSpec + MapLibre + 制图引擎，不平行重写任何组件。

## 变更总览（11 个功能提交 + 快照，均原子）

| 里程碑 | 内容 | 关键文件 |
|---|---|---|
| M1/M2 查询契约 v2 | 单字段排序（v2 cursor 绑定 order 摘要）、typed 谓词 AST（allowlist op + 深度/项数/值数门）、精确 total_matching（>250k 诚实 null）、truncated/is_complete/reason_codes、分组聚合端点（null 键/非数值/__other__ 披露，>500k 422） | `app/services/feature_pages.py`、`feature_aggregate.py`（新）、`app/api/routes/layer.py` |
| M3 MVT 位置要素 id | 仅无链内稳定 id 的要素编码 field 1（=FC 自然索引+1，revision 内稳定）；有 id 数据集瓦片字节不变（ETag/缓存奇偶）；双编码路径+no-shapely 回退字节一致；AM 分裂件同 id；feature_id_index 懒构建 O(1) 单要素寻址（first-wins 重复语义） | `app/services/mvt.py` |
| M4 TablePanel 服务端分页 | MVT 层从「无内联属性」占位改为真实分页浏览：首屏/游标续页/服务端排序循环/搜索 or-contains 下推/视口窗口/诚实计数（已显示·匹配·共）/位置 id 与重复 id 披露/409 重载/429 退避/查询身份变化作废；inline 路径字节不变 | `frontend/components/map/map-components/table-panel.tsx`、`lib/map-components/paged-table-data.ts`（新） |
| M5/M6 联动收口 | MVT 点击→详情链位置 id 兜底（链内 404→positional 重试一次）；$id ↔ 瓦片 field 1 ↔ 行 id 三方同源；分页表排序/搜索/视口全为瞬态状态，绝不写回 MapSpec | `frontend/lib/store/layer-data.ts` |
| M7/M8 视口聚合 | MVT 层 extentLinked 走服务端 /aggregate（全扫口径窗口真值，替代 ≤5000 采样）；证据行（扫描/匹配/分组/revision）+ truncated 徽标 + null 键披露；generation 取消；inline 层 Wave5 本地过滤零改动（回归护栏） | `frontend/lib/map-components/viewport-aggregate.ts`（新）、`chart-panel.tsx` |
| M9/M10 导出标注收敛（双孪生） | ①priority=重要度名次（priorityField 数值大先保，live Top-N 同向；修掉「priority=收集序」的名字丢失缺陷）②线 repeat（symbol-placement=line 等距 ≤4 锚点，对齐 MapLibre symbol-spacing）③CJK 折行（≈8em、≤2 行 tspan；单行字节稳定） | `app/services/mapspec_to_svg.py` ↔ `frontend/lib/mapspec-compiler/mapspec-to-svg.ts`、`label_typography.py` ↔ `label-geometry.ts` |
| M13 QA 接线 | map_critique 新增 check_export_label_diagnostics：deterministic 碰撞+出版导出时以确定性编译 diagnostics 产出证据级 findings（budget 超限/抑制过半=warning，截断=info）；条件不满足/编译失败=诚实缺席 | `app/services/gis_harness/map_critique.py` |
| M14 Harness 工具 | webgis_layer_features_page / webgis_layer_aggregate / webgis_layer_feature_detail：Agent 有界分页读面（limit≤200、无任意表达式、scope_note 强制窗口语义） | `app/tools/layer_browse_tools.py`（新） |
| M15 | 三档性能基准（5k/50k/200k 真实计时）+ 场景 5 端到端一致性（图表数据==聚合通道计数/组件齐全/标签编译） | `tests/perf/`、`tests/cartography/test_trackb_scenario_consistency.py` |

## 与 open PR 的边界（实时核对 2026-10-31）

- **#1870（design choices）**：未触碰 design_candidates*/component_registry/semantic_checks*/component_renderers 的 _SUPPORT_MATRIX 区/renderer-parity-matrix 表体。M13 走 map_critique.py（#1870 未涉及）。
- **#1871（atlas publish）**：未触碰 publication_export.py/atlas_batch/*。标注改动只在单页编译器层（mapspec_to_svg），#1871 的页编译消费该产物自动受益。
- **#1873（feature editing）**：未触碰 map-panel.tsx/use-feature-selection.ts/feature_edit/*。位置 id 与 WFID 是两个显式区分的命名空间（identity 语义只读对齐 FEATURE_ID_KEYS 链）；位置 id 不进入写通道。
- **共享生成物**：openapi snapshot/api-docs/`_TOOL_MODULES`/categories.py 各一处 additive 行，独立提交（cd342d4ed / a1782bab7）便于 rebase。
- 前端：table-panel/chart-panel/selection-store/viewport-context/paged-features 在 6 个 open PR 中零占用；catalogue.ts/sceneCommands.ts/messages/map.json 全部避开（新文案走新命名空间 mapTable/mapChart）。

## 测试与证据

- 新增测试 100+：后端 58（契约/HTTP/工具/critique/标注/场景/基准）+ 前端 43（hook/组件/孪生）。
- 回归全绿：既有 feature_pages/MVT/边界/孪生/golden corpus/parity/layout/PDF/tool catalog/i18n key-completeness（后端 850+，前端 343 selection/data-plane/map-components 域 + 287 compiler 域）。
- 性能实测（200k 要素，本机 Python 3.13）：首页 p50=0.6ms、排序首建 689ms（后续 (ref,rev,order) 缓存命中）、过滤页 2.3ms、精确 total 587ms、聚合全扫 365ms、MVT 瓦片（334 点含位置 id）26.6ms。完整三档数字见 `planning/cartography-linked-views/EVIDENCE.md`。
- 命令：`pytest tests/unit/test_feature_query_v2.py tests/test_layer_query_v2_http.py tests/unit/test_mvt_positional_id.py tests/unit/test_layer_browse_tools.py tests/cartography/test_label_trackb_vnext.py tests/perf/test_trackb_paging_benchmark.py`；前端 `pnpm exec vitest run lib/map-components/ components/map/map-components/table-panel-paged.test.tsx components/map/map-components/chart-panel-agg.test.tsx lib/mapspec-compiler/`。

## 诚实披露（非伪一致）

- live/export 碰撞引擎机制差异（GPU 逐帧 vs 确定性单遍）是架构声明，不做像素级伪验收；本次收敛的是**专业错误级**差异（priority 收集序、线单贴、CJK 不折行）。
- 晕圈色：live haloMode=auto 随渲染期底图亮度自适应；导出遵循 spec 声明值 —— 差异来源是渲染时机，披露不假装。
- total_matching 未知时显式 null + total_unknown（绝不用 returned 假冒）；聚合分母/非数值/null 键全部随行披露。

## 已知限制

1. 表格行→地图 flyTo 未接线（组件级相机通道不存在；map-panel.tsx/bus.ts 为 #1873 占用区）——行点击发布 selection（带 id_field/bbox），相机消费留给后续。
2. chart 来源选择对分页表只过滤已加载页（诚实窗口语义）。
3. Redis/磁盘后端的 200k 端到端未在本机复现（HTTP 层 mock store 验证协议 + 服务函数真实 200k FC 验证计算）。

## 复现

```bash
git fetch origin && git checkout zcode/carto-linked-views-labels-vnext
uv venv .venv && uv pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q -n 0 tests/unit/test_feature_query_v2.py tests/test_layer_query_v2_http.py tests/unit/test_mvt_positional_id.py
cd frontend && pnpm install && pnpm exec vitest run lib/map-components/ --maxWorkers=2
```
