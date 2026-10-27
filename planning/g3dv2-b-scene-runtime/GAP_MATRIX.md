# G3DV2-B GAP_MATRIX — 真缺口（代码级 file:line 证据）

缺口分级：P0 = 静默错读/证据缺失级；P1 = 资源治理/生命周期级；P2 = 交互/体验级；P3 = 记录级。

## G1 纯 3D 无真实 ACK（B1，P0）
- 门：`frontend/lib/hooks/use-cartographic-observation.ts:146-165` —— `_mapspecFingerprint` 只由 HUD 图层行携带（`chat/adapters.ts:364,384`、`layerCommands.ts:178`、`map-state-restore.ts:226`）；纯 3D（layers: []）会话 fp=undefined → `if (!fingerprint) return`（:165）永不 POST。
- 后端丢弃：`app/schemas/chat_schema.py:248-312` DTO 无 `scene3d_assets` 字段，pydantic 静默丢弃前端 `render-observation.ts:392-396` 附加的块（`grep scene3d_assets app/` 零命中）。
- envelope 盲区：`render_work_envelope.py:243-413` 只投影 sources/layers/components，scene.assets 不产生 work 项；scene-only spec 的 ACK `layers[]`/`components[]` 均空。
- 直测旁路存在：`runtime.scene3d.test.ts:196-214` 绕过 hook 直调 collectRenderObservation —— 证明采集面已就绪，缺的是 hook 门 + 后端 schema。

## G2 glTF 交错/sparse/normalized 静默错读（B2，P0）
- **byteStride 从未被读取**：`gltf-parser.ts` `decodeAccessor`(:277-327) 按 `compBytes*arity` 隐式紧密步长顺序读；`GltfJson['bufferViews']` 接口声明了 byteStride(:107) 但全库无读取点 → 交错 GLB "解析成功"但顶点为垃圾数据，无拒绝无警告（decodeIndices :329-364 同理）。
- **sparse 不在词表**：`JsonAccessor`(:139-145) 无 sparse 字段；带 bufferView+sparse 的 accessor 只读 base 漏 delta；无 bufferView 的 sparse POSITION 返回全零(:299-302) → build.ts 静默构建退化网格。
- **normalized 从 accessor 未读**：按调用点假设（仅 TEXCOORD_0 允许 :403）；integer POSITION/NORMAL 被拒但 code 误导(:314-316)；normalized UV 仅支持 5121/5123(:318-325)。

## G3 跨资产累计预算缺失（B3，P1）
- `decodedBytes` 是 `parseGltf` 局部变量（gltf-parser.ts:274-298）逐 parse 归零；无跨 tile/跨资产账本。乘积上界 = 每资产 96 LRU tile × 5M 三角/256MB 解码 × N 资产。
- in-flight 仅 per-asset（`mounted.loadInFlight` asset-manager.ts:85,562）—— N 资产 = N×4 并发下载，无全局信号量。
- 无 geometry/texture/material bytes、CPU decoded、visible tiles 的累计执行面；无 pressure 状态机。

## G4 生命周期状态不完整（B1，P1）
- status ring（status.ts:9-15）六码 `mounted|unmounted|loading|load_failed|unavailable|degraded`：**无 `cancelled`**（abort 在 mountAsset catch :424 静默吞掉后只记泛化 unmounted :327）、**无 disposed 区分**（map teardown :285-292 同样记 unmounted）、**无 context-lost stale 标记**。
- MountedAsset(:63-87) 无 state 字段；`describe()`(:295-304) 从对象存在性推导 —— 无权威当前态查询。

## G5 WebGL context lost/restored 面（B1，P1）
- 无 `webglcontextrestored` 监听（全库 grep 零命中）；恢复=用户点击重试 → mapKey bump 全量重建（map-panel.tsx:364-374,1644）。
- **refCount 泄漏（真实 bug）**：setStyle 清扫 custom layer 不触发 onRemove → `unmountAsset` 的 removeLayer 抛错被吞（asset-manager.ts:325）→ `releaseRenderer` 永不调；重挂时 `acquireRenderer` 对陈旧 carrier refCount++（custom-layer.ts:44-47）→ 每次 setStyle 循环 refCount 膨胀，共享 THREE renderer 永不 dispose；死亡 layer 的 group 也留在 carrier.scene。

## G6 3D Tiles 严格性（B2，P1）
- `refine` 非法值静默 coerce 为 REPLACE（tileset.ts:229-230）；refine 无父子继承校验（OGC：子未声明继承父）。
- URI 策略分裂：`asset-manager.ts:777-787 resolveUriSafe`（接受 same-origin http）vs `resource.ts:37-46 isAllowedUrl`（仅 https）。
- tile 内容 `.gltf` 外部 buffer 无 loader（loadTile :582 裸调 parseGlf）→ typed 拒绝（可接受但未披露为能力边界）。

## G7 工作台 3D 交互缺口（B5，P2）
- scene-controller.tsx 无 per-asset 控制（visible/opacity/LOD 均无）、无 lighting 控制（custom-layer.ts:63-64 硬编码）、无相机视角保存。
- i18n 缺口：`scene-disclosure.tsx:30-46` 硬编码 zh COPY；3D 拾取卡硬编码英文（map-panel.tsx:1702-1727）。
- a11y：SceneController 无 Escape 关闭、无焦点圈闭、radio 无方向键语义（:197-296）。
- j9 toggle 定位器 `切换至 3D 视角` 与实际 key `切换3D视图` 不匹配（journey :164 vs messages zh-CN map.json:17）→ 该步永不触发。

## G8 已有且不需要重做（负证据，防重复）
- 相机保持：2D↔3D 切换不写 center/zoom（map-panel.tsx:714-740）；style 切换保相机（transform 保持）；user-wins 仲裁在 viewCommands.ts:31-180。
- StoryMap 场景/中断：story-view.tsx:164-212,270-306 复用 commitUserScene；手势暂停 gated on originalEvent。
- 导出披露：exporter.ts:2132-2221 sceneExportDisclosure + chromeDegradations 齐备；统计图/图例/比例尺与 3D 无耦合（grep 零命中）。
- setStyle 存活重挂：asset-manager.ts:192-199 已有（getLayer 缺席检测）。

## G9 性能/验收债（B0/B6，P3→随 M6 清偿）
- 生产 bundle 逐 chunk 对比未做；j1–j8 未本地重跑；无 GPU 真机（SwiftShader only，诚实披露）。
