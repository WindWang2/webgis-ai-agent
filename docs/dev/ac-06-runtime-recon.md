# AC-06 运行时勘察（adaptive-cartography/06-symbol-law-runtime）

基线 commit：`1fd4b035`（origin/master @ 2026-09-13）。分支 `adaptive-cartography/06-symbol-law-runtime`。
勘察方式：S1 只读扫描（Explore subagent，一次）+ 主 agent 精读核心文件 + 本地 perf harness 采样。

## 1. 符号常量清单

完整销项表见 [ac-06-symbol-constants.csv](./ac-06-symbol-constants.csv)（41 行）。头部常量：

| 位置 | 常量 | 现值 | 处置 |
|---|---|---|---|
| `renderer.ts:413` | fill-opacity | 0.8 | P1 → `opacityForCount(count)` |
| `renderer.ts:416` | circle-opacity | 0.8 | P1 同上 |
| `renderer.ts:417` | circle-radius | 6 | P1 → `circleRadiusExpression(zoom, count)` |
| `renderer.ts:510` | resolveHeatmapRadiusPx 默认 | 30px 常量（clamp [4,80]） | P1 → `heatmapRadiusExpression(zoom, count, basePx)` |
| `renderer.ts:538` | heatmap-intensity | 已随 zoom（4/10/14 → 0.8/1.3/2.2） | 保留，作为符号律 zoom 停靠基准 |
| `renderer.ts:771` | dash 图案 | [4,2]/[1,2]/[4,2,1,2] | P4 canonical `dashArray`（显式意图，不改命令式路径） |
| compiler.ts:438-444 | cluster 半径 step | 14/20/26/34@10/50/200 | 已按 count 分级，保留 |
| adapter.ts / selection-highlight / annotationHelpers | 各类 UI 常量 | — | 交互 UI/标注意图非数据符号学，**保留**（CSV 逐行有 disposition） |

grep 销项断言（P8）：`renderer.ts` 中 `fill-opacity.*0.8`、`circle-radius.*6`（字面量）、`resolveHeatmapRadiusPx` 的常量返回 30 —— 改造后归零（词法上以符号律调用替代）。

## 2. recompile 触发路径全集（reconciler.ts `diffSpecs` :128）

`LayerChange.kind ∈ {add, remove, recompile, filter}`，产生 **recompile（remove+add）** 的入口恰三个：

1. **源定义变更**（:179-185）：`changedSourceIds.has(layer.source)` —— 源对象深不等（含 inline GeoJSON 逐坐标比较，:157）→ 该源全部依赖层 recompile。**先于 filter 快路径判定**（源更新必须赢）。
2. **层字典深不等**（:186-191）：`!isDeepEqual(prevLayer, layer)` —— paint/layout/label/type/sourceLayer/cluster/visible 任一变化都整层 remove+re-add（ADR-0036 Q3 明示不做逐属性 diff）。**唯一例外**：仅 `filter` 变化走 `isFilterOnlyChange`（:102-107）→ `setFilter` 快路径（ADR-0091）。
3. **初次 null prev** 不算（add）。

消费侧（runtime.ts）：`applyPatchDirect` :245（同步）/ `applyPatchDebounced` :336（去抖）按 remove→sources→add→filter→z-order 次序执行；`removeLayerSafe` :664 连带 `${id}-label` 幽灵层。**改色（paint）现在必然整层闪烁**——P3 的靶心。
`setPaintProperty/setLayoutProperty` 在 reconcile 路径**从未使用**，仅命令式 `renderer.updateLayerStyle` :677 使用。

## 3. 性能基线（10k 要素；本机 2026-09-13，vitest/jsdom，performance.now 中位数，RUNS=15）

Harness：`frontend/lib/mapspec-compiler/mapspec-diff.perf.test.ts`（确定性种子几何，断言只钉 patch 形状不钉时间）。

| 场景 | P0 基线 (ms) | 含义 |
|---|---|---|
| `compile_10k` | **12.662** | compileMapSpec 全量编译（10k 点 + 2k 面） |
| `diff_paint_only` | **0.007** | 改色（源同引用）——层字典走查，本就便宜 |
| `diff_one_changed` | **6.232** | 12k 特征中改**最后一个**特征属性（全量走查最坏情况） |
| `diff_rev_equal` | **9.681** | 内容相同、对象重建（新 identity）——完整等价走查 |

P7 目标：`diff_one_changed` 与 `diff_rev_equal` 经 content_revision 短路 + WeakMap 哈希快败后显著下降（可量化对比表）；`compile_10k` 不劣化（±10% 内）；10k 交互（改色）**零 removeLayer/addLayer**（事件计数断言）。

帧率说明：本仓无浏览器端 10k 帧率设施（S1 确认）；以 diff/compile CPU 耗时 + render-debouncer FrameStats（10ms 帧预算，`render-debouncer.ts:46`）为代理指标，P7 以「改色 patch 的 op 数与 MapLibre 调用计数」补强。

## 4. layer type × StyleMethod 组合矩阵（compiler.ts :313-416 if/else 链）

`StyleMethodType = constant | interpolate | step | match | field`（types.generated.ts:11）；
层类型并集（:137）`circle | line | fill | symbol | heatmap | raster | fill-extrusion`。

| 层类型 | paint 分支 | 缺口 |
|---|---|---|
| circle | ✅ :314-324（color/radius/opacity/stroke*） | blur/translate 无 |
| line | ✅ :325-331（color/width/opacity） | **dasharray/blur/translate 无** |
| fill | ✅ :332-338（color/opacity/strokeColor→outline-color） | translate 无；outline **width** MapLibre 契约不支持（P4 evidence） |
| heatmap | ✅ :339-385（radius/opacity/color→ramp/intensity/weight + 原生键透传） | — |
| raster | ✅ :386-391（仅 opacity） | — |
| fill-extrusion | ✅ :392-414（color/opacity/height/base + 透传） | — |
| **symbol** | ❌ **无分支 → 静默空 paint** | P4 补 canonical 分支 |
| **background** | ❌ 类型并集都没有 | P4 schema+编译器+运行时 |
| **hillshade** | ❌ 同上（连带缺 raster-dem 源类型） | P4 同上 |

`compileStyleMethod` :47-58：interpolate **仅 `["linear"]`**（无 exponential/cubic-bezier）；`field:"zoom"` 特判缺失。paint-bridge `CANONICAL_PAINT_KEYS`（:22-55）同构缺 symbol/background/hillshade。

## 5. 未知 source 类型现状（必须改显式报错）

- compiler.ts:279-285 else 分支：**静默空 FeatureCollection**（吞掉 data_fabric/wms/wmts/pmtiles 的 url/dataPath 形态）。
- runtime.ts `applySource` :601 else 尾注释「nothing to apply」静默 no-op。
- adapter.ts:180-183 未知 source 形状同病（adapter 本线不动，编译器路径修复即可满足契约）。

## 6. diff 实现与短路素材

- 深比较 `isDeepEqual`（reconciler.ts:65-89）朴素递归；源 diff（:157）对 inlineData **逐坐标走查**。
- 快路径 `isMapSpecShallowEqual`（:120-126）仅当 sources/layers **对象引用**相等（compose memo 化时成立）。
- `content_revision?: number` 已在契约上（types.generated.ts:99），但只作 MVT 瓦片 cache-buster（tile-url.ts:6 等 4 处），**diff 从不消费**——P5 短路的现成钩子。
- 无任何 content_hash 设施；`mutation_revision` 是文档级 CAS，与层无关。
- `syncLayerZOrder`（renderer.ts:1020-1050）：每个 baseId 的子层按 rank 排序后**逐层 moveLayer(id)**（O(全部子层) 次调用，#692 已记账 z-order O(n·m)）。

## 7. legend_spec / paint 桥现状

- thematic-paint.ts：四模式（graduated/continuous/divergent/categorical）→ 仅产出**颜色**表达式；`nodata` 有 guard（:160-163）；**legend_spec v2 新字段（unit/method/k/…）无人消费**；未知 mode → null → 静默回落平色。
- paint-bridge.ts:144-152：**无法映射的键直接丢弃**（原文注释「无法映射的键丢弃」）——P6 改为 evidence。

## 8. 05 线领地确认（禁改红线）

- `frontend/lib/mapspec-runtime/label-layout.ts` **尚不存在**（05 线未落地）。
- runtime.ts 标注函数段：`addLabelSublayerSafe` :710-743（整函数）、`removeLayerSafe` 中 label 幽灵清理 :670-676、`applyFilterSafe` 中 label 镜像 :695-700、`addLayerSafe` 内 labelSpec 判定与调用 :643-651 —— **本线不改这些行**。
- compiler.ts label 子层段 :496-525、`label-solver.ts` —— 不动。

## 9. 复核纪要（§0.2 防重复）

- PR 检索（symbol/paint/renderer/reconcile/zoom/radius，200 条）：唯一在途相关线为 **03 线 PR #1258**（adaptive symbology engine + legend_spec v2，ADR-0152，OPEN）。性能线 PR #370（render hot paths，MERGED 2026-08-14）已覆盖 reconcile work-count（map-render-work-count.test.tsx 即其产物）——本线不重复其 work-count 断言，P3 计数器在其上加补丁类型维度。
- Issue 检索：#692（z-order O(n·m)、同 spec 无早退——已由 #375/#462 部分修复，本线 P5 补最小移动集）、#411（z-order 重排修复，MERGED）。
- 分支检索：仅 `adaptive-cartography/03-adaptive-symbology` 在途；无 symbol-law 同名实现。
- 代码 grep：`circle-radius`/`fill-opacity 0.8`/`recompile`/`interpolate` 分布与上文矩阵一致，无既往符号律实现。
- ADR 约束：ADR-0118/0126（Cartographic Rendering V5/V6）确立 MapSpec 声明式契约与 typed paint；ADR-0138（契约版本化 V9）确立 schema 单一真相在 `app/lib/cartography/mapspec_schema.py`，TS 侧由 `ts_projection.py` 生成——**P4 的类型扩展必须走 schema 胶水 + 再生成**（§8 允许的胶水范畴，决策记录进 ac-06-decisions.md）。
- 结论：无重复实现风险；与 03 线接口 = legend_spec v2 schema（其分支置顶 `docs/dev/ac-03-legend-spec-v2.schema.json`，P6 按其本地 fixture 驱动）；`density_signal()` 05 线未落地，**本线实现，05 线后续 import**。

## 10. ADR 编号

master 最高 ADR-0147；03 线占 ADR-0152；**ADR-0155 空闲 → 本线占用**（水线核查 2026-09-13）。
