# Semantic Zoom / Representation-at-Scale Runtime（方向 5）

**Status**: implemented · **MapSpec contract**: v1.6 (additive) · **ADR anchor**: 本文档（dev note，随 PR 存档）

## 0. 问题（current-master 证据）

五套 zoom 分档并存且互不对账（c11-typed-cartoir-design.md:24-26 原始记录）：

| 分档 | 边界 | 权威 | 渲染消费 |
|---|---|---|---|
| label/scale 语义带 | 0/8/11/14/24 | `label_plan.DEFAULT_ZOOM_BANDS` ≡ `scale_rules.SCALE_TIERS`（双契约锁） | label 重抽稀 ✓；表达动作 ✗ |
| scene LOD | 3/6/10/14/19 | `scene_lod.LOD_BANDS` | **零生产接线** |
| data-plane LOD | 8/12 | `frontend/lib/data-plane/plan.ts` | 装载调度（display-only） |
| 源级 zoom | 0/14 缺省 | compiler clusterMaxZoom 缺省 | ✓（缺省硬编码） |
| QA 视口近似 | — | `semantic_checks._viewport_bbox` | 服务端 QA |

三条断链：`layer.visibility.{min_zoom,max_zoom,hints}`（v1.5）前端完整消费但**后端零生产**；`visibility_hints`（scale_rules tier → grammar → layer_meta）**前端零消费**；`resolveDensityPresentation`（native/cluster/heatmap）只在 headless 编译器——**live runtime 无密度裁决**（live/export 表达分歧）。

## 1. 方案：单一派生协议（不新建第二真相）

```
Map purpose + medium + viewport/zoom + dataset descriptor + user pins
   → representation.representation_at_scale()          [C2，组合既有权威]
   → RepresentationAtScale（windows 计划 + 提示 + reason/evidence/披露）
   → MapSpec layer.representation.windows (v1.6)        [C3，desired state 声明]
     + layer.visibility 结构门投影（converter 落笔）
   → carto-ir/representation.ts（前端归一化/查询，单一裁决器）
   → compiler + runtime：窗口编译为 MapLibre 原生 minzoom/maxzoom 门 + 子层
   → export：export zoom 处一次裁决（Python/TS 孪生同语义 + diagnostics） [C6]
   → semantic_checks carto.scale.representation + quality_loop report    [C7]
```

### 所有权（唯一新增 = 表达协议词表 + 窗口计划）

- 带边界/dense 阈值/候选序：`scale_rules`（不动）；标注密度：`label_plan`（只读投影）；LOD：`scene_lod`（只读投影）；几何×数量提示：`gis.scale_semantics`（不重复）。
- 新模块 `app/lib/cartography/representation.py` 只拥有：`REPRESENTATION_MODES` 词表、`RepresentationAtScale`/`RepresentationWindow` 工件、归一化/查询/导出裁决函数。schema 常量 `mapspec_schema.REPRESENTATION_MODES` ≡ 协议模块（契约测试），manifest 双闸锁 TS 镜像。

### 关键设计决策

1. **窗口编译而非运行时切换**：表达切换编译为 MapLibre 原生 `minzoom/maxzoom` 门（零 per-frame JS、零 React rerender）；zoom 手势不回写 MapSpec（不是 mutation）。
2. **主层 = 首个 raw 窗**（门 ∩ 结构门）；无 raw 窗的计划主层退化为身份锚（`minzoom=24` 恒空门——保 HUD/z-order/用户显隐级联的 id 锚点）。非首窗 → `${id}__rep${i}` 子层（`__` 前缀进 `matchMapLayers` 用户显隐级联 + `syncLayerZOrder` 子层分组）。
3. **cluster 窗** → 源级聚合（clusterMaxZoom = 簇窗终点；MapLibre 创建期一次配置）+ 官方三子层（`__clusters`/`__cluster-count`，门 = 簇窗 ∩ 结构门）。单消费者纪律与既有密度推断同款（共享源降级 raw 克隆 + evidence）。**簇资格按 spec 层型判定**（首窗 heatmap 改写编译层型不影响数据面语义）。
4. **user-wins**：converter 的显式 type_hint（point_cluster/heatmap）→ user pin（单一窗口整程生效）；authored `representation` 恒不被覆盖；不合法 pin（几何不适用/词表外/artifact 未声明）保留记录 + 披露，绝不静默改/丢。
5. **视觉聚合 ≠ 分析聚合**：聚合窗必带 `AggregationSemantics(scope="visual_only")` + 固定披露文案（`VISUAL_AGGREGATION_DISCLOSURE`）；QA 断言聚合窗必有披露。
6. **artifact capability seam**：`aggregate_grid`/`generalized` 只在调用方声明可用时入窗；不可用 → honest fallback raw + `REPR.FALLBACK.*` 码。street 带永不 generalized（粗工件不适合最细尺度）。不写新泛化算法。
7. **hysteresis**（C1）：`resolve_scale_tier_stable(zoom, previous_tier, margin=0.25)` —— 纯函数死区（无时钟无状态）；带内留守、跨界需越过 margin、词表外前带回退无记忆。
8. **物理比例尺**：`meters_per_pixel`/`scale_denominator`（Web Mercator 256px 契约；纬度钳 ±85.0511；DPI fail-closed 96；z0 赤道 96dpi ≈ 1:591,658,710 标准）。
9. **导出 parity**（C6）：export zoom = `spec.view.zoom`（authored；缺省 8）。Python `export_adjudicate_layer` 与 TS 孪生同语义（render/render_heatmap/gated/hidden）；门外层不进导出 + `layer_gated_by_zoom`/`layer_representation_hidden` 诊断（权威词表注册）；`expected_semantics` 增加 `scale_omitted_layers`/`export_zoom` 对账面。
10. **hidden 不复活**：计划层的 paint/layout 增量 patch 回落整层 recompile（子层与主层 visibility 由重建联动）；`removeLayerSafe` 注册表级联删子层；计划移除后拆源级聚合残留（ghost config）。

## 2. 兼容性

- v1.6 纯 additive（identity upgrader）；旧 spec 无 `representation` 键 → 行为零变化（既有 corpus golden 零漂移验证）。
- 平凡全 raw 计划不落笔（converter `project_mapspec_representation` 返回 None）。
- 密度推断（`resolveDensityPresentation`）只在无显式计划时介入——显式声明优先。

## 3. 测试闸

- 后端：`test_representation_protocol_v1.py`（67：边界±ε/hysteresis 死区/比例尺/窗口推导/pin/归一化/导出裁决）、`test_representation_corpus_v1.py`（24 case golden 重算）、`test_mapspec_schema_v16_representation.py`、`test_representation_projection_v1.py`（converter/QA/report）、`test_export_representation_parity_v1.py`（孪生）。
- 前端：`representation.test.ts`（34 裁决器）、`representation-compile.test.ts`（编译展开）、`runtime-representation.test.ts`（live 展开/清理/显隐/拆配）、`mapspec-to-svg-representation.test.ts`（导出裁决）、`representation-parity.golden.test.ts`（**跨语言 parity**：TS 裁决器 ≡ python 冻结 golden，24×12）。
- manifest 双闸：`representation_modes` 后端 pytest ≡ JSON ≡ `CONTRACT_REPRESENTATION_MODES`。
- 语义检查契约：`carto.scale.representation` 注册（44→45 码，契约 JSON 显式更新）。

## 4. 非目标（deferred）

- 泛化算法本体（Simplify/DP/Visvalingam）——只建 seam；
- `aggregate_grid`/`generalized` 的后端工件生产管线；
- scene_lod 的 3D runtime 接线（本方向只读投影）；
- `boundary_detail` 的渲染执行（仍 advisory；词表键 `admin_boundary_detail` 保持非门控披露）。
