# G3DV2-B INTERFACE_CONTRACTS — 契约面变更（additive 清单）

## 前端
- `SceneAssetStatusCode` += `'cancelled' | 'context_lost'`（封闭词表扩展，observation `scene3d_assets.code` 同源）。
- `Scene3dSyncOptions` += `budgetLimits?: Partial<ResourceBudgetLimits>`。
- `Scene3DManager` 公开面 += `handleContextLost()/handleContextRestored(spec)/budgetUsage()/setAssetPresentation(assetId, patch)`。
- scene3d index/renderer 门面 += `scene3dContextLost/scene3dContextRestored/scene3dBudgetUsage/scene3dSetAssetPresentation/scene3dApplyLightingPreset`、`presetLighting`、`Scene3dLightingPreset`。
- custom-layer += `releaseLayerOwnership(map, group)/forceDisposeCarrier(map)/applyLightingPreset/presetLighting`。
- `ParsedGltf.stats` += `geometryBytes/textureBytes/materialCount`（消费方增量只读）。
- `RenderWorkEnvelope`（TS）+= `scene_work: RenderWorkSceneAssetItem[]`（≤32）；SOURCE_KINDS += `model3d`（镜像遗漏补齐）。
- `useCartographicObservation` 指纹门第三级回退：`HUD 行 → 会话游标（同会话）→ 执行信封（revision 对齐）`。
- `SceneController` props += `getMap?`；`map-panel` 拾取卡标签 i18n（`map:scene3dPick.*`）。

## 后端（最小 additive）
- `render_work_envelope.py`：`SceneAssetWork` dataclass + `scene_work[]`（≤32 截断+诊断）+ 进 `work_fingerprint` 语义字段 + `MAX_SCENE_WORK=32` 导出。
- `chat_schema.py CartographicRuntimeObservationRequest` += `scene3d_assets: Optional[list[dict]]`（validator：≤16 条、code 封闭词表 8 值、asset_id≤64/reason≤120、非法→None 证据缺席；只保留 `{asset_id, code, reason?}` 投影）。
- `chat.py` 观察落账 += `scene3d_assets` 键（在场才落）。
- corpus：`contract_manifest.json` += `render_work.max_scene_work`；golden s13 场景；OpenAPI 快照刷新（含 #1830 storymap 两端点欠账）。

## 不变量（未动）
- `RENDER_WORK_SCHEMA_VERSION`/`ACK_SCHEMA_VERSION` 版本号不变（字段级 additive，消费方按缺席兼容：TS 解析对无 scene_work 的旧信封产出空数组）。
- ACK 无新增块（scene 证据走 observation `scene3d_assets` + envelope `scene_work` + 既有 `sources[]` 对账 —— 不建第二 ACK）。
- 2D 会话观察/修复回路行为零变化（HUD 指纹优先级与既有语义不变）。
- corpus s01–s12 语义断言不变（仅计数 12→13 与 work_fingerprint 再生值）。
