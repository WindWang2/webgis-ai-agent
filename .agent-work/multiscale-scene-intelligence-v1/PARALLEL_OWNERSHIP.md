# PARALLEL_OWNERSHIP — 热文件/接口依赖矩阵（执行时点快照）

## 1. Open PR 触碰面 vs 本方向计划触碰面

| 文件/目录 | #1335 | #1336 | 本方向 | 冲突处置 |
|---|---|---|---|---|
| `app/services/gis_harness/evidence_claim/**` | ✏️ | — | — | 零接触 |
| `app/services/gis_harness/hotpath_convergence/**` | ✏️ | — | — | 零接触 |
| `app/services/mission_runtime/**` | ✏️ | — | — | 零接触 |
| `app/lib/modelops/geoai/**`, `app/tools`（geoai 部分） | — | ✏️ | — | 零接触 |
| `frontend/components/geoai/**` | — | ✏️ | — | 零接触 |
| `app/lib/cartography/mapspec_schema.py` | — | — | ✏️ additive | 独占 |
| `app/lib/cartography/scene_*.py`（新） | — | — | ✏️ 新文件 | 独占 |
| `app/services/mapspec/lifecycle_engine.py` | — | — | ✏️ 加 intent 分支 | 独占（#1335/#1336 均不触） |
| `frontend/lib/map-kit/renderer.ts` / `adapter.ts` / `map-panel.tsx` | — | — | ✏️ 3D 接线 | 独占 |
| `frontend/lib/mapspec-compiler/types.generated.ts` | — | — | ✏️ 再生成 | 由 ts_projection.py 生成，语义冲突源= schema（独占） |
| `tests/unit/test_*.py`（新 scene 测试） | — | — | ✏️ 新文件 | 独占 |
| `CHANGELOG.md` / `UBIQUITOUS_LANGUAGE.md` | — | — | ✏️ append | 文本级无冲突 |
| `docs/adr/0198-*.md` | — | ✏️（0197/0198 已用） | ✏️ 用 **0199+** | 编号避开 |

注：#1336 的 ledger 显示其 ADR 编号用到 0197/0198 —— 本方向 ADR 从 **0199** 起。

## 2. 共享但只做 additive 的接口（依赖方清单）

### `mapspec_schema.py`（+1.4 版本）
- 消费方：ts_projection（再生成）、parse_mapspec（golden corpus）、frontend compiler、
  mapspec_to_svg 孪生、export/report 冷路径。
- 纪律：只加 Optional 字段 + identity upgrader；`KNOWn_VERSIONS` 追加 "1.4"；
  golden corpus 必须 byte-stable（旧 spec 输出不变）。

### `lifecycle_engine.py`（+intent 分支）
- 消费方：mapspec_store facade、gis_world_state mutation、API routes、healer。
- 纪律：新 intent 只在 `_apply_intent_cow` 加分支；不触碰锁/CAS/校验骨架；
  `MutationIntent` union 追加成员（ discriminated by `intent` 字段）。

### `frontend/lib/mapspec-runtime/adapter.ts`（自动挤出证据门控）
- 消费方：runtime.reconcile（diff/patch）。
- 纪律：保持无 spec scene 时行为逐字节不变（is3D 旧路径在无 scene 契约时回退）。

### `renderer.ts` enable3DTerrain（DEM 源参数化）
- 消费方：map-panel 3D effect、comparison-view、测试 mock。
- 纪律：默认参数不变（AWS terrarium 兜底保留 = MapLibre fallback 始终可用）；
  spec 提供 terrain 源时优先 spec。

## 3. 禁止重复施工清单（任务书 + 勘察确认）

- ❌ 第二套 MapSpec/MapProductGraph/storymap 重写/Cesium 强绑/新 WebGL 引擎
- ❌ 第二套 DEM 获取（复用 stac_client/spectral_engine）
- ❌ 第二套 legend/classification（复用 thematic_spec 单源）
- ❌ 第二套 screenshot/critic 管线（复用 runtime_validator/visual_judge）
- ❌ #1330–#1334 的 fail-closed 修复面（#1335 已占）
- ❌ GeoAI 面（#1336 已占）
