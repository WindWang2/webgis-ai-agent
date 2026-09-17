# CURRENT_ARCHITECTURE — 现有制图/渲染链（2026-09-16, master faa453a8）

## 1. 意图 → 计划 → 产品 → MapSpec → 渲染 主链

```
MapRequestIntent (gis_harness/intent.py:132)
  → MapProductPlanner.plan_from_intent (planner.py:666)
  → MapProductSpec (product_spec.py:241)  [views/relations/delivery, revision=编辑计数]
  → product_compiler.compile_product_spec (product_compiler.py:170)
  → 组装工具走 mapspec_store facade (app/services/mapspec_store.py:93)
  → apply_gis_mutation (gis_world_state/mutation.py:378)
      user-wins 守卫 → MapSpecLifecycleEngine.apply_mutation (lifecycle_engine.py:1420)
        session 锁 → mutation_id dedup → expected_revision CAS → COW intent 分发
        → review_and_repair_cartography → validate_mapspec → checkpoint → save(revision+1)
  → 持久化 mapspec_store (services/mapspec/store.py:160, disk+Redis, revision CAS token)
  → 前端 SSE commitMapSpecDocument (use-sse-stream.ts:560)
  → composeLiveMapSpec (live-spec.ts:154) → MapSpecRuntime.reconcile (runtime.ts)
  → MapLibre v5 (react-map-gl) + chrome DOM (map-spec-chrome.tsx)
```

## 2. 3D 相关现有路径（三条互不相连的孤岛）

### 2a. fill-extrusion 数据面（后端驱动，spec 承载）
`create_3d_extrusion_map` 工具 → `metadata.extrusion` + `type_hint:"extrusion_3d"` →
`analysis_cartography_converter` 写 MapLibre paint 表达式（extrusion_model.py 生成）→
`MapSpecLayer(type="fill-extrusion")` → 前端 compiler/runtime 原生渲染。
高度来源=**要素属性字段**（`height_field`），与 DEM 无关。

### 2b. 前端 is3D 布尔（用户手动，与 spec 无关）
`useHudStore.is3D` → `map-panel.tsx:429-441` effect → `renderer.enable3DTerrain(map)`
（**硬编码 AWS terrarium DEM**，exaggeration 1.5，pitch 60/bearing 20 easeTo）→
`adapter.ts:409-417` 把每个普通 polygon 层自动挤出（height=`coalesce(get height, 20)`）。
comparison-view 同步 is3D。后端只通过 SSE 回传 `is_3d` 观测（use-sse-stream.ts:1231）。

### 2c. DEM 分析面（后端 terrain 科学栈）
STAC Copernicus → compute_terrain/slope/hillshade → 服务端预渲染 PNG →
MapSpec `type:"raster"` 层（raster_cartography_converter）。MapLibre 原生 hillshade
（raster-dem 源）只在 spec 显式给 raster-dem 源时由前端挂载（runtime.ts:853-863），
上传 raster 的 hillshade 路径明确"未接线"（raster_cartography_converter.py:195）。

## 3. 质量闭环

- 确定语义检查：`semantic_checks.py`（EXTRUSION_HEIGHT_FIELD_VALID / DISTRIBUTION /
  PITCH_ADVISORY / OCCLUSION_WARNING）在 lifecycle 的 review_and_repair 内运行。
- VLM 评判：visual_judge v2（5 轴，fail-closed not_evaluated 语义，scores 派生不信模型）。
- 指标棘轮：cartography_ratchet（scene×check p66 基线 ±5%）。
- 自愈：visual_healer 4 类 micro-mutation（presentation-only）经
  ApplyVisualHealPatchIntent 事务挂载；运行时 AUTO_SAFE 修复在 cartography_runtime。
- 截图证据：runtime_validator → Playwright map.png → critic。

## 4. 导出链（3D 感知点）

- PNG/PDF：前端 live canvas（is3D 时 terrain 生效，`terrain_3d_scale_caveat` 披露比例尺不可靠）。
- 真 SVG：双孪生把 fill-extrusion 压平为 fill（`3d_perspective_not_vectorized`）、
  hillshade 披露 `hillshade_not_vectorizable`。

## 5. 本 PR 的接入点（不替换任何既有件）

- **scene planning**：新纯函数模块 `app/lib/cartography/scene_*.py`（无 I/O、可确定性测试）。
- **MapSpec 契约**：v1.4 additive（view.scene?、terrain?、extrusion 证据字段、camera 建议位）。
- **mutation**：新增 intent 走既有 apply_mutation 管线（锁/CAS/dedup/checkpoint 免费获得）。
- **前端**：spec 驱动 terrain/scene 模式（替换硬编码源；保留 MapLibre fallback）；
  adapter 自动挤出改为证据门控。
- **质量门**：新确定性 scene gate 挂入 semantic_checks/质量闭环词汇表；VLM 契约零改动（extra=forbid）。
- **自愈**：新增 scene 缺陷类别但只映射到**既有** mutation intents（SetView/PatchLayerPresentation…）。
