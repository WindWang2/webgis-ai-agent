# PARALLEL_OWNERSHIP — 极限规模渲染运行时 V2（执行时点快照 2026-09-16T20:19Z, base faa453a8）

## 1. Open PR 触碰面 vs 本方向计划触碰面矩阵

图例：✏️=会写；👀=只读依赖；—=不触。#1356 触碰面经 `gh pr diff 1356 --name-only` 逐文件核实。

| 文件/目录 | #1335 | #1336 | #1351 | #1352 | #1353 | #1354 | #1355 | #1356 | 本方向 | 冲突处置 |
|---|---|---|---|---|---|---|---|---|---|---|
| `app/services/mvt.py` | — | — | — | — | — | — | — | — | ✏️ **独占**（新增 LOD/渐进构建, 不改 encoder 核心） | 独占 |
| `app/api/routes/layer.py` | — | — | — | — | — | — | — | ✏️（terrain-tiles 路由 + 租户面） | ✏️ additive 新端点 | **冲突**：#1356 在同文件加路由。本方向只 append 新路由函数/新 import，不改既有行；落地时以 #1356 版本为 rebase 基准 |
| `app/services/raster_tile_service.py` | — | — | — | — | — | 👀 | — | ✏️（terrarium/terrain tile） | —（本方向不碰 raster 渲染） | 零接触 |
| `app/services/session_data*.py` / `ref_payload_cache.py` / `ref_lifecycle.py` | 👀(tenant) | — | — | — | — | — | — | — | ✏️ 仅 additive（如需 hook 注册点） | 独占 |
| `app/schemas/ref_descriptor.py` | — | — | — | — | — | — | — | — | ✏️ additive（descriptor 可选字段如 lod_hint） | 独占 |
| `app/lib/cartography/data_tiers.py` | — | — | — | — | — | — | — | 👀 | 👀 import（不改常量） | 只读；改常量走 ADR（非本方向） |
| `app/lib/cartography/scene_*.py`、`mapspec_schema.py` | — | — | — | — | — | — | — | ✏️ 独占 | — | **零接触（#1356 禁碰）** |
| `app/services/mapspec/**`（lifecycle/store/coordinator） | — | — | — | — | — | — | — | ✏️ | — | 零接触 |
| `app/services/data_quality/**`、`app/lib/data/quality.py` | — | — | ✏️ | — | — | — | — | — | — | 零接触 |
| `app/evaluation/**`、`scripts/gis_bench_v2.py` | — | — | — | ✏️ | — | — | — | — | ✏️ scripts/perf/**（不同目录） | 命名避让：本方向 benchmark 用 `scripts/perf/extreme_scale_*` / `tests/benchmarks/test_extreme_scale_*.py` |
| `frontend/components/geoai/**`、modelops/geoai、embedding cache | — | ✏️ | — | — | — | — | — | — | — | **热区勿碰** |
| `frontend/components/cockpit/**`、`frontend/lib/cockpit/**`、workbenchSlice | — | — | — | — | ✏️ | — | — | — | — | 零接触 |
| `frontend/lib/map-kit/renderer.ts` | — | — | — | — | — | — | — | ✏️（3D/terrain 接线、scene LOD 消费） | ✏️ 谨慎 additive | **高冲突**：本方向尽量不改 renderer.ts 本体；新逻辑放新模块（`frontend/lib/map-kit/progressive/`），renderer.ts 仅允许 +1 行接线式 import/调用（见 DECISIONS.md） |
| `frontend/lib/mapspec-runtime/adapter.ts` / `runtime.ts` | — | — | — | — | — | — | — | ✏️ | ✏️ 谨慎 additive | 同上：adapter 只加 source 类型分发分支一处；runtime 只在 applySource 加一个 source-kind 分支；不触碰 reconciler/diff/恢复骨架 |
| `frontend/lib/mapspec-compiler/**`（types.generated.ts 等） | — | — | — | — | — | — | — | ✏️ | —（若需 source 类型扩展，走新字段 + 不再生成 types.generated.ts 的手工 additive；能不碰就不碰） | 冲突面，默认零接触 |
| `frontend/components/map/map-panel.tsx` | — | — | — | — | — | — | — | ✏️ | ✏️ 谨慎 additive | 高冲突：本方向只在 handleMove 的 100ms debounce 结算块（map-panel.tsx:1144-1155 附近）追加一个 progressive-engine 通知调用；不重排既有 effect |
| `frontend/lib/hooks/use-sse-stream.ts` | — | — | — | — | — | — | — | — | ✏️ additive（descriptor 透传/渐进参数） | 独占 |
| `frontend/lib/map-kit/tile-url.ts` / `tile-auth.ts` | — | — | — | — | — | — | — | — | ✏️ additive（URL 参数） | 独占 |
| `frontend/lib/mapspec/ref-source-resolver.ts` | — | — | — | — | — | — | — | — | ✏️ | 独占 |
| `app/services/gis_harness/**`、`mission_runtime/**` | ✏️ | — | — | — | — | — | ✏️(spatial_events/portfolio) | — | — | 零接触 |
| rs temporal cube（rs/**） | — | — | — | — | — | ✏️ | — | — | — | 零接触 |
| `CHANGELOG.md` / `UBIQUITOUS_LANGUAGE.md` | 概率✏️ | ✏️ | 概率✏️ | 概率✏️ | 概率✏️ | 概率✏️ | 概率✏️ | ✏️ | ✏️ append | 文本追加，冲突可解 |
| `docs/adr/` | — | 0198 | — | — | — | — | — | 0199 | ✏️ **0200 起** | 编号避让 |

## 2. 本方向独占声明（预期新增/改写，他人勿动）

- **新目录** `frontend/lib/map-kit/progressive/**`：viewport-aware progressive loading 编排、LOD 选取、patch 应用、worker 池封装、内存/网络预算器、退化控制器。
- **新文件** `app/services/tile_pipeline.py`（命名候选，见 DECISIONS.md）：服务端 LOD 金字塔 / 视口要素查询 / patch 计算的纯服务层（mvt.py 保持 encoder+cache 职责，只 additive）。
- **新路由**：`app/api/routes/layer.py` 内 append（如 `GET /layers/data/{ref_id}/view` 或 `/tiles/{z}/{x}/{y}/lod{n}.mvt`），零改既有函数体。
- **新测试**：`tests/unit/test_tile_pipeline*.py`、`tests/benchmarks/test_extreme_scale_*.py`、`frontend/lib/map-kit/progressive/*.test.ts`。
- **ADR-0200+**（编号避让 #1336=0198、#1356=0199）。

## 3. 禁碰清单（Hot zones）

- ❌ `frontend/components/geoai/**`、`app/lib/modelops/**`、embedding cache（#1336）
- ❌ `app/lib/cartography/scene_*.py`、`app/lib/cartography/mapspec_schema.py`、`app/services/mapspec/**`、`mapspec-compiler/types.generated.ts`、`scene-compiler*`（#1356）
- ❌ `app/services/data_quality/**`、`app/lib/data/quality.py`（#1351）；`app/evaluation/**`、`scripts/gis_bench_v2.py`（#1352）；`frontend/{components,lib}/cockpit/**`、workbenchSlice（#1353）；`app/services/spatial_events/**`、portfolio 路由（#1355）；`app/services/gis_harness/evidence_claim/**`、`mission_runtime/**`（#1335）
- ❌ 不重写 MVT encoder（mvt.py 的 protobuf/投影/裁剪核心）；不建新 Data Fabric；不做第二套 MapSpec/storymap/导出管线
- ❌ ADR-0154 的 `label.zoomBands` / `thresholds.maxFeatures` 承载面 = #1356 的 scale-aware LOD（**样式/标注面**）。本方向 LOD 全部在**数据面**（几何/要素分级、加载粒度），不读不写这两个字段——边界已与 #1356 PR 描述核对（其 PR body 明言"落 ADR-0154 既有承载面，零新 spec 字段"）。

## 4. 共享但只 additive 的接口（依赖方纪律）

- `app/api/routes/layer.py`：只 append 路由；租户校验/预算调用必须复用 `require_owned_session` + `_layer_data_budget`（layer.py:52/76）；ETag/304 对齐 `_tile_response`（layer.py:250）。
- `frontend/lib/mapspec-runtime/runtime.ts applySource`：新 source-kind 只加一个 `else if` 分支（对齐 raster-dem 先例, runtime.ts:853-863）；无 spec 字段时行为逐字节不变。
- `renderer.ts`：若必须改，仅限新增 import + 一处函数尾追加调用；F31/viewport/diff 既有守卫（renderer.ts:22/37/155）语义不得回退。
- `use-sse-stream.ts`：只透传新 descriptor 字段与参数，不改 SSE 事件处理顺序。
