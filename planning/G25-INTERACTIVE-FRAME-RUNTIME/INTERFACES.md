# G25 INTERFACES — 新增/消费接口契约

## 新增模块（本方向所有）

### 1. `frontend/lib/cartography/frame-runtime.ts`（P0 契约，纯函数）

```ts
/** 画幅挂载状态机（词表封闭，A12/快照≠live 的基础） */
type FrameMountState = 'unmounted' | 'loading' | 'live' | 'snapshot' | 'stale' | 'error';

interface FrameRuntimeSlot {
  frameId: string;
  mountState: FrameMountState;
  /** 期望渲染的 spec revision（committed generation 序） */
  desiredSpecRevision: number;
  /** 当前渲染已落定的 spec revision（live/snapshot 时有意义） */
  resolvedSpecRevision: number | null;
  /** 数据绑定 content_revision（resolved 同义） */
  desiredDataRevision: number | null;
  resolvedDataRevision: number | null;
  syncGroup: string | null;
  /** 本地 camera 草稿（sessionStorage 恢复；永不进 schema） */
  cameraDraft: CameraSnapshot | null;
  /** 同组期望相机（未挂载帧由 hub 记录） */
  expectedCamera: CameraSnapshot | null;
  /** 快照指纹：session+frame+specRev+dataRev+viewportSize */
  snapshotFingerprint: string | null;
  /** 渲染实例代际（context loss 重建后 +1） */
  generation: number;
  error: { code: string; message: string } | null;
}

/** 唯一只读运行时投影：committed spec × 快照store × 激活态 → slots */
function projectFrameRuntime(spec, opts): FrameRuntimeSlot[]

/** 状态推导纯函数（snapshot 只能在版本匹配时保持，旧 revision → stale） */
function deriveMountState(slot, currentRevisions): FrameMountState

/** 快照指纹（稳定 hash：session|frame|specRev|dataRev|w×h） */
function snapshotFingerprint(input): string

/** active frame 失效（删除/禁用/解绑）后的确定性后继 */
function selectActiveSuccessor(frameIds, removedId, order): string | null

/** 投影兼容判定：frame 视图投影 ≠ 宿主投影 → {compatible:false, reason} */
function checkProjectionCompat(frameView, hostProjection): {ok, reason?}
```

### 2. `frontend/lib/cartography/where-ast.ts`（P0，后端 evaluate_where 的 JS 同源移植）

```ts
/** 与 app/services/feature_pages.py 同词表：叶 eq/ne/in/between/contains/
 * startswith/gte/gt/lte/lt + 组合 and/or/not；同型比较、bool≠number、
 * 字符串字典序窗口、contains/startswith 大小写不敏感、eq null 命中缺席。 */
export function evaluateWhereAst(where: WhereNode, feature: {properties?: Record<string, unknown>}): boolean
export function filterFeaturesByWhere(features: Feature[], where: WhereNode | null): Feature[]
```

### 3. `frontend/components/map/frames/frame-camera-hub.ts`（P3）

```ts
/** 模块级相机事件总线（每 workbench 一个实例，grid 持有） */
class FrameCameraHub {
  /** 注册挂载帧（含主图联动成员） */
  attach(entry: {id, kind: 'main'|'frame', map, syncGroup, generation}): () => void
  /** 相机事件入口（move 事件回调注册在 attach 内部完成） */
  // 内部: source+seq+generation → resolveGroupSync(复用) → 批量 applyCameraPatch
  // 防回声: (1) 容差收敛 null (2) per-target writeEpoch 门 (3) 陈旧 seq 丢弃
  /** 未挂载帧的期望相机记录（同组事件 → expectedCamera 更新 → 快照失效钩子） */
  noteUnmounted(frames: Array<{frameId, syncGroup}>): void
  setSyncDimensions(pan: boolean, zoom: boolean): void
  dispose(): void
}
```

### 4. `frontend/components/map/frames/frame-instance-registry.ts`（P1）

```ts
/** 全局 maplibre 实例登记（主图/画幅/对比副图统一计数，A09） */
interface FrameInstanceRegistry {
  register(kind: 'main'|'frame'|'comparison', id: string): {generation: number, release: () => void}
  liveCount(): number
  subscribe(cb): () => void   // 计数变化通知（grid 调度依据）
}
export const mapInstanceRegistry: FrameInstanceRegistry
export const DEFAULT_LIVE_MAP_BUDGET = 2  // 计主图；可配置（e2e/诊断）
```

### 5. `frontend/lib/cartography/frame-snapshot-store.ts`（P1/P4）

```ts
interface SnapshotEntry {
  frameId: string; fingerprint: string; dataUrl: string;
  bytes: number; specRevision: number; dataRevision: number | null;
  viewportSize: {width, height}; capturedAt: number;
}
/** LRU：项数上限（默认 12）+ 字节上限（默认 32MiB）；fingerprint 命中才有效 */
class FrameSnapshotStore {
  put(entry): 'stored'|'evicted_self'|'rejected_oversize'
  get(frameId, fingerprint): SnapshotEntry | null
  invalidateFrame(frameId): void
  invalidateSession(sessionId): void   // A14 切会话无残留
  stale(frameId, currentRevisions): boolean
  stats(): {count, bytes, hits, misses}
}
```

### 6. `frontend/components/map/frames/frame-viewport.tsx`（P1/P3/P4 组件）

react-map-gl `<Map>` + 独立 `MapSpecRuntime`；spec = 帧有效文档（where 过滤在
source 注入前应用于内联 GeoJSON features）；`webglcontextlost` → generation+1
重建 + 观察重绑；卸载时 canvas 捕获 → snapshotStore.put。

### 7. `frontend/components/map/frames/frame-grid.tsx`（P1/P2/P5 编排组件）

网格容器（CSS grid columns=产品配置）；激活槽=FrameViewport；其余=快照瓦片
（fingerprint/版本标注 + 非live徽标）；键盘 [ ]/F 切换 + Escape 关闭；scope
命名徽标（仅本幅/同组/整个产品）；挂载点=map-panel 兄弟节点（同 ComparisonView）。

### 8. `frontend/lib/cartography/frame-observation.ts`（P6）

```ts
interface FrameObservationRow {
  frame_id: string;
  mode: 'live_observed' | 'snapshot' | 'not_observed' | 'stale_observation' | 'export_verified';
  mapspec_revision: number;       // resolved（渲染落定）而非 desired
  content_revision?: number | null;
  fingerprint?: string;           // 快照指纹（snapshot 模式必带）
  camera?: {center, zoom};        // refs-only
  source_error?: string;          // 有界
  observed_at: number;
}
/** 页面挂点：window.__g25FrameObservation() → rows[]（e2e/G28 读取，只读） */
/** 上报：并入既有 cartographic-observation 请求体 frames 字段（加性） */
export function collectFrameObservations(): FrameObservationRow[]
```

### 9. 后端（P6，加性）

- `CartographicRuntimeObservationRequest.frames: Optional[list[dict]]`（≤16 行，
  与 scene3d_assets 同款增维）。
- `frame_products/verification.py`：`observed` 生产者接线——`frame_product_status`
  从 `_cartographic_observation.frames` 读取（服务端 fingerprint 门已盖章 revision）。
- 只读工具（P5，仅必要时）：`frame_product_runtime_status` —— 从 status 投影
  画幅运行态（不写授权面）。

## 消费的既有接口（只读/既有语义）

- `getCommittedMapSpec`/`subscribeMapSpecLive`/`getMapSpecLiveGeneration`（session-cursor）
- `composeLiveMapSpec`/`injectResolvedRefSources`/`subscribeRefSources`（live-spec/ref-source-resolver）
- `MapSpecRuntime`（mapspec-runtime）
- `resolveGroupSync`/`syncGroupMembers`（frame-sync）+ comparison-sync 原语
- `projectFrameProduct`/`commitFrameProductPatch`（frame-product）
- `registerEscapeHandler`（escape-stack）
- e2e：`bootstrapMock`/`frameProductCommitTurn`/`JourneyWorld`/registry
