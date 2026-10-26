/**
 * scene3d/asset-manager — 每地图一份的 3D 资产生命周期管理器（G3D02 M4/M6）。
 *
 * 职责（唯一事实：MapSpec scene.assets；本管理器只做投影与执行）：
 * - `sync(spec)`：id + source revision 键控的幂等差分挂载（增/删/改/显隐）；
 *   scene.mode === '2d' 时资产卸载（诚实降级；footprint 表达归矢量层）。
 * - glTF/GLB：解析 → build → 单层挂载；3D Tiles：tileset → SSE 调度
 *   （事件驱动：move/rotate/pitch + 加载完成再触发；无自建 rAF 循环）→
 *   tile 缓存 LRU（身份 = tile id + 资产 revision）+ 并发上限 + AbortController。
 * - dispose：geometry/material/texture 全释放；tileset abort；状态环留痕。
 * - pick：逆 MVP 造 ray（世界空间）→ 各资产局部（米）Raycaster → PickEvent。
 */

import * as THREE from 'three';
import type { MapSpec, Model3dMapSpecSource, SceneAssetSpec } from '@/lib/mapspec-compiler/types.generated';

import {
  buildPlacementMatrix,
  frustumPlanesFromMat4,
  invertMat4,
  planeToLocalSpace,
  worldToLngLatAlt,
  type Mat4,
  type WorldCoord,
} from './geo';
import { buildModel, type BuiltModel } from './gltf/build';
import { GltfParseError, parseGltf } from './gltf/gltf-parser';
import { makeScene3dCustomLayer } from './custom-layer';
import { fetchBinary, fetchJson, Scene3dResourceFailure } from './resource';
import { recordSceneAssetStatus } from './status';
import { parseTileset, TilesetParseError, type ParsedTileset, type TileNode } from './tiles3d/tileset';
import { scheduleTiles } from './tiles3d/scheduler';

export interface Scene3dSyncOptions {
  /** 机器档位硬顶（spec lod 只能收紧不能放宽）。 */
  machineMaxVisibleTiles?: number;
  /** 弱机 SSE 下限（越大越粗）。 */
  machineMinSse?: number;
  /** 调度节流（ms；默认 200）。 */
  scheduleThrottleMs?: number;
  /** tile 并发加载上限（默认 4）。 */
  maxConcurrentTileLoads?: number;
  /** tile 缓存上限（默认 96）。 */
  tileCacheSize?: number;
}

interface TileEntry {
  tile: TileNode;
  model: BuiltModel | null;
  loading: boolean;
  lastUsed: number;
}

interface LodPolicyView {
  maxSse: number;
  maxVisibleTiles: number;
}

interface MountedAsset {
  assetId: string;
  sourceId: string;
  format: 'gltf' | 'glb' | '3dtiles';
  revision: string;
  visible: boolean;
  lodPolicy: LodPolicyView;
  /** 模型局部（米）→ mercator 世界（含 glTF 轴系校正）。 */
  placement: Mat4;
  group: THREE.Group;
  singleModel: BuiltModel | null;
  tileset: ParsedTileset | null;
  tiles: Map<string, TileEntry> | null;
  prevRender: Set<string>;
  controller: AbortController;
  tickTimer: ReturnType<typeof setTimeout> | null;
  lastTickAt: number;
  /** 最近帧 MVP（拾取/眼位/视锥消费）。 */
  lastMvp: Mat4 | null;
  loadInFlight: number;
  /** 挂载世代（sync 差分时旧代在途回调作废）。 */
  generation: number;
}

export interface Scene3dPickHit {
  assetId: string;
  sourceId: string;
  tileId: string | null;
  nodeName: string | null;
  extras: unknown;
  lngLat: { lng: number; lat: number; altitudeM: number };
  /** 相机到命中点距离（米）。 */
  distanceM: number;
}

type MapLike = {
  addLayer?: (layer: unknown, beforeId?: string) => void;
  removeLayer?: (id: string) => void;
  getLayer?: (id: string) => unknown;
  on?: (event: string, handler: () => void) => void;
  off?: (event: string, handler: () => void) => void;
  triggerRepaint?: () => void;
  getCanvasContainer?: () => { clientHeight: number };
  getContainer?: () => { clientHeight: number };
  queryTerrainElevation?: (lngLat: { lng: number; lat: number }) => number | null;
} & object;

const managers = new WeakMap<object, Scene3DManager>();

export function getScene3dManager(map: object): Scene3DManager {
  let m = managers.get(map);
  if (!m) {
    m = new Scene3DManager(map as MapLike);
    managers.set(map, m);
  }
  return m;
}

function assetRevisionKey(source: Model3dMapSpecSource): string {
  const rev = source.content_revision;
  return typeof rev === 'number' && Number.isFinite(rev)
    ? `r${rev}`
    : source.data_fingerprint ?? source.descriptor_fingerprint ?? 'r0';
}

function policyOf(asset: SceneAssetSpec, options: Scene3dSyncOptions): LodPolicyView {
  const specSse = typeof asset.lod?.max_sse === 'number' && asset.lod.max_sse > 0 ? asset.lod.max_sse : 16;
  const specTiles = typeof asset.lod?.max_visible_tiles === 'number' && asset.lod.max_visible_tiles > 0
    ? Math.floor(asset.lod.max_visible_tiles)
    : 128;
  // 机器档位：SSE 取下限（弱机更粗）、tile 上限取 min —— spec 只能收紧。
  return {
    maxSse: Math.max(specSse, options.machineMinSse ?? 0),
    maxVisibleTiles: Math.min(specTiles, options.machineMaxVisibleTiles ?? 128),
  };
}

export class Scene3DManager {
  private mounted = new Map<string, MountedAsset>();
  private moveHandler: (() => void) | null = null;
  private disposed = false;

  constructor(
    private readonly map: MapLike,
    private readonly options: Scene3dSyncOptions = {},
  ) {}

  /** 幂等差分同步（MapSpec → 挂载集）。返回本次变更摘要（观察面）。 */
  sync(spec: MapSpec | null | undefined): { mounted: number; removed: number; updated: number } {
    if (this.disposed) return { mounted: 0, removed: 0, updated: 0 };
    const assets = spec?.scene?.assets ?? [];
    const sources = spec?.sources ?? {};
    const sceneActive = !!spec?.scene && spec.scene.mode !== '2d';

    const wanted = new Map<string, SceneAssetSpec>();
    if (sceneActive) {
      for (const asset of assets) {
        if (asset && typeof asset.id === 'string' && asset.id) wanted.set(asset.id, asset);
      }
    }

    let mountedCount = 0;
    let removedCount = 0;
    let updatedCount = 0;

    for (const assetId of [...this.mounted.keys()]) {
      if (!wanted.has(assetId)) {
        this.unmountAsset(assetId);
        removedCount += 1;
      }
    }

    for (const [assetId, asset] of wanted) {
      const sourceEntry = sources[asset.source] as Model3dMapSpecSource | undefined;
      if (!sourceEntry || sourceEntry.type !== 'model3d') {
        // coordinator 在写路径阻塞；运行到这里的只可能是恢复面旧档 → typed 降级。
        recordSceneAssetStatus(assetId, 'unavailable', 'source_missing_or_not_model3d');
        if (this.mounted.has(assetId)) {
          this.unmountAsset(assetId);
          removedCount += 1;
        }
        continue;
      }
      const revision = assetRevisionKey(sourceEntry);
      const existing = this.mounted.get(assetId);
      if (existing) {
        if (existing.sourceId !== asset.source || existing.revision !== revision) {
          this.unmountAsset(assetId);
          removedCount += 1;
        } else {
          const visible = asset.visible !== false;
          if (existing.visible !== visible) {
            existing.visible = visible;
            existing.group.visible = visible;
            updatedCount += 1;
          }
          const nextPlacement = this.placementOf(asset, existing.format);
          if (!matEquals(nextPlacement, existing.placement)) {
            existing.placement = nextPlacement;
            this.applyPlacement(existing);
            updatedCount += 1;
          }
          existing.lodPolicy = policyOf(asset, this.options);
          continue;
        }
      }
      void this.mountAsset(assetId, asset, sourceEntry);
      mountedCount += 1;
    }

    if (this.mounted.size > 0 && !this.moveHandler) {
      this.moveHandler = () => this.scheduleAll();
      this.map.on?.('move', this.moveHandler);
      this.map.on?.('rotate', this.moveHandler);
      this.map.on?.('pitch', this.moveHandler);
    } else if (this.mounted.size === 0 && this.moveHandler) {
      this.detachMoveHandler();
    }

    return { mounted: mountedCount, removed: removedCount, updated: updatedCount };
  }

  /** 拾取：屏幕像素（CSS 像素，相对 canvas）→ 3D 命中；无命中 null。 */
  pick(px: number, py: number, canvasWidth: number, canvasHeight: number): Scene3dPickHit | null {
    const ray = this.rayFromPixel(px, py, canvasWidth, canvasHeight);
    if (!ray) return null;
    const raycaster = new THREE.Raycaster();
    raycaster.ray.origin.set(ray.origin.x, ray.origin.y, ray.origin.z);
    raycaster.ray.direction.set(ray.direction.x, ray.direction.y, ray.direction.z);

    let best: { hit: THREE.Intersection; asset: MountedAsset } | null = null;
    for (const asset of this.mounted.values()) {
      if (!asset.visible) continue;
      const inv = invertMat4(asset.placement);
      if (!inv) continue;
      const localRaycaster = new THREE.Raycaster();
      localRaycaster.ray.copy(raycaster.ray).applyMatrix4(new THREE.Matrix4().fromArray(inv));
      const targets: THREE.Object3D[] = [];
      if (asset.singleModel) {
        asset.singleModel.root.traverse((o) => { if ((o as THREE.Mesh).isMesh) targets.push(o); });
      }
      if (asset.tiles) {
        for (const entry of asset.tiles.values()) {
          if (entry.model) entry.model.root.traverse((o) => { if ((o as THREE.Mesh).isMesh) targets.push(o); });
        }
      }
      // 局部空间 = 米制（placement 含米→世界换算），distance 即米。
      const hits = localRaycaster.intersectObjects(targets, false);
      if (hits.length > 0 && (!best || hits[0].distance < best.hit.distance)) {
        best = { hit: hits[0], asset };
      }
    }
    if (!best) return null;
    const mesh = best.hit.object as THREE.Mesh;
    const meta = (mesh.userData?.scene3d ?? {}) as { nodeName?: string | null; extras?: unknown };
    const worldPoint = transformPoint(best.asset.placement, {
      x: best.hit.point.x, y: best.hit.point.y, z: best.hit.point.z,
    });
    return {
      assetId: best.asset.assetId,
      sourceId: best.asset.sourceId,
      tileId: typeof mesh.userData?.scene3dTileId === 'string' ? mesh.userData.scene3dTileId : null,
      nodeName: meta.nodeName ?? null,
      extras: best.hit.object.userData?.scene3dTileExtras ?? meta.extras ?? undefined,
      lngLat: worldToLngLatAlt(worldPoint),
      distanceM: best.hit.distance,
    };
  }

  /** 释放全部（map unmount / WebGL 重建路径调用）。 */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const assetId of [...this.mounted.keys()]) {
      this.unmountAsset(assetId);
    }
    this.detachMoveHandler();
  }

  /** 观察面：当前挂载摘要。 */
  describe(): { assetId: string; format: string; visible: boolean; tilesReady: number | null; state: string }[] {
    return [...this.mounted.values()].map((a) => ({
      assetId: a.assetId,
      format: a.format,
      visible: a.visible,
      tilesReady: a.tiles ? [...a.tiles.values()].filter((t) => t.model).length : null,
      state: a.singleModel || (a.tiles && a.tileset) ? 'mounted' : 'loading',
    }));
  }

  // ── 内部 ───────────────────────────────────────────────────────────────

  private detachMoveHandler(): void {
    if (!this.moveHandler) return;
    this.map.off?.('move', this.moveHandler);
    this.map.off?.('rotate', this.moveHandler);
    this.map.off?.('pitch', this.moveHandler);
    this.moveHandler = null;
  }

  private unmountAsset(assetId: string): void {
    const asset = this.mounted.get(assetId);
    if (!asset) return;
    asset.generation += 1; // 作废在途回调
    asset.controller.abort();
    if (asset.tickTimer) clearTimeout(asset.tickTimer);
    for (const entry of asset.tiles?.values() ?? []) entry.model?.dispose();
    asset.singleModel?.dispose();
    asset.group.clear();
    try { this.map.removeLayer?.(`scene3d-${assetId}`); } catch { /* setStyle 已清除 */ }
    this.mounted.delete(assetId);
    recordSceneAssetStatus(assetId, 'unmounted');
  }

  private async mountAsset(assetId: string, asset: SceneAssetSpec, source: Model3dMapSpecSource): Promise<void> {
    const format = source.format;
    recordSceneAssetStatus(assetId, 'loading', format);
    const mounted: MountedAsset = {
      assetId,
      sourceId: asset.source,
      format,
      revision: assetRevisionKey(source),
      visible: asset.visible !== false,
      lodPolicy: policyOf(asset, this.options),
      placement: this.placementOf(asset, format),
      group: new THREE.Group(),
      singleModel: null,
      tileset: null,
      tiles: null,
      prevRender: new Set<string>(),
      controller: new AbortController(),
      tickTimer: null,
      lastTickAt: 0,
      lastMvp: null,
      loadInFlight: 0,
      generation: 0,
    };
    this.mounted.set(assetId, mounted);
    const generation = mounted.generation;

    const customLayer = makeScene3dCustomLayer({
      assetId,
      map: this.map,
      group: mounted.group,
      hooks: {
        onFrame: (mvp) => {
          if (mounted.generation !== generation) return;
          mounted.lastMvp = mvp.slice() as Mat4;
        },
      },
      onRenderError: () => {
        if (mounted.generation === generation) {
          recordSceneAssetStatus(assetId, 'degraded', 'render_error');
        }
      },
    });
    try {
      this.map.addLayer?.(customLayer as never);
    } catch {
      recordSceneAssetStatus(assetId, 'unavailable', 'custom_layer_rejected');
      this.unmountAsset(assetId);
      return;
    }
    mounted.group.visible = mounted.visible;
    this.applyPlacement(mounted);

    try {
      const baseUrl = typeof source.url === 'string' && source.url ? source.url : null;
      if (!baseUrl) {
        // ref_id 水合链未供给 —— capability 声明 partial 的诚实边界。
        throw new Scene3dResourceFailure(
          'url_rejected',
          String(source.ref_id ?? ''),
          'data fabric hydration for model3d refs not yet available',
        );
      }
      if (format === '3dtiles') {
        await this.loadTileset(mounted, asset, baseUrl, mounted.controller.signal);
      } else {
        const buffer = await fetchBinary(baseUrl, { signal: mounted.controller.signal });
        if (mounted.generation !== generation) return;
        const parsed = await parseGltf(buffer, {
          externalBufferLoader: (uri) => fetchBinary(uri, { signal: mounted.controller.signal }),
        });
        if (mounted.generation !== generation) return;
        const built = buildModel(parsed);
        mounted.singleModel = built;
        mounted.group.add(built.root);
        this.applyPlacement(mounted);
        for (const w of parsed.warnings) recordSceneAssetStatus(assetId, 'degraded', w);
        recordSceneAssetStatus(assetId, 'mounted', `${parsed.stats.triangles}tri`);
      }
      this.map.triggerRepaint?.();
      this.scheduleAll();
    } catch (err) {
      if (mounted.generation !== generation || mounted.controller.signal.aborted) return;
      const reason = err instanceof GltfParseError || err instanceof TilesetParseError || err instanceof Scene3dResourceFailure
        ? `${err.name}:${err.message}`.slice(0, 120)
        : String(err).slice(0, 120);
      recordSceneAssetStatus(assetId, 'load_failed', reason);
      this.unmountAsset(assetId);
    }
  }

  private async loadTileset(
    mounted: MountedAsset,
    asset: SceneAssetSpec,
    tilesetUrl: string,
    signal: AbortSignal,
  ): Promise<void> {
    const doc = await fetchJson<unknown>(tilesetUrl, { signal });
    if (mounted.controller.signal !== signal || mounted.controller.signal.aborted) return;
    const anchor = asset.placement?.anchor ?? [0, 0];
    const parsed = parseTileset(doc, {
      tilesetUrl,
      anchor: { lng: anchor[0], lat: anchor[1] },
      resolveUri: resolveUriSafe,
    });
    mounted.tileset = parsed;
    mounted.tiles = new Map<string, TileEntry>();
    collectTiles(parsed.root, mounted.tiles);
    recordSceneAssetStatus(mounted.assetId, 'mounted', `${parsed.tileCount}tiles`);
    this.scheduleAsset(mounted, true);
  }

  private placementOf(asset: SceneAssetSpec, format: 'gltf' | 'glb' | '3dtiles'): Mat4 {
    const anchor = asset.placement?.anchor ?? [0, 0];
    let altitudeM = asset.placement?.altitude_m ?? 0;
    if (asset.placement?.ground_clamp) {
      const elev = this.map.queryTerrainElevation?.({ lng: anchor[0], lat: anchor[1] }) ?? null;
      if (typeof elev === 'number') {
        altitudeM += elev;
      } else {
        recordSceneAssetStatus(asset.id, 'degraded', 'ground_clamp_without_terrain');
      }
    }
    return buildPlacementMatrix(
      {
        anchor: [anchor[0], anchor[1]],
        altitudeM,
        headingDeg: asset.placement?.heading_deg ?? undefined,
        pitchDeg: asset.placement?.pitch_deg ?? undefined,
        rollDeg: asset.placement?.roll_deg ?? undefined,
        scale: asset.placement?.scale ?? undefined,
      },
      { format },
    );
  }

  /** placement 应用到渲染图（单模型直接挂矩阵；tileset 由外层 group 承载）。 */
  private applyPlacement(mounted: MountedAsset): void {
    mounted.group.matrix.fromArray(mounted.placement);
    mounted.group.matrixAutoUpdate = false;
    // matrixAutoUpdate=false 下矩阵直改必须显式传播（renderer 不感知）。
    mounted.group.matrixWorldNeedsUpdate = true;
    if (mounted.singleModel) {
      mounted.singleModel.root.matrix.identity();
      mounted.singleModel.root.matrixAutoUpdate = false;
      mounted.singleModel.root.matrixWorldNeedsUpdate = true;
    }
    this.map.triggerRepaint?.();
  }

  private scheduleAll(): void {
    for (const asset of this.mounted.values()) this.scheduleAsset(asset, false);
  }

  private scheduleAsset(mounted: MountedAsset, immediate: boolean): void {
    if (!mounted.tileset || !mounted.tiles || this.disposed) return;
    const throttleMs = this.options.scheduleThrottleMs ?? 200;
    const now = Date.now();
    const run = () => {
      mounted.tickTimer = null;
      this.runSchedule(mounted);
    };
    if (immediate) {
      if (mounted.tickTimer) clearTimeout(mounted.tickTimer);
      run();
      return;
    }
    if (now - mounted.lastTickAt < throttleMs) {
      if (!mounted.tickTimer) {
        mounted.tickTimer = setTimeout(run, throttleMs - (now - mounted.lastTickAt));
      }
      return;
    }
    run();
  }

  private runSchedule(mounted: MountedAsset): void {
    const tileset = mounted.tileset;
    const tiles = mounted.tiles;
    if (!tileset || !tiles) return;
    mounted.lastTickAt = Date.now();
    const canvasH = this.canvasHeight();

    const eye = this.eyeFromMvp(mounted.lastMvp);
    if (!eye) return; // 首帧未渲染：等 onFrame 提供矩阵
    const eyeEnu = worldToEnu(mounted.placement, eye);

    const worldPlanes = mounted.lastMvp ? frustumPlanesFromMat4(mounted.lastMvp) : null;
    const enuPlanes = worldPlanes
      ? worldPlanes.map((p) => planeToLocalSpace(p, mounted.placement))
      : null;

    const loadedSet = new Set<string>();
    for (const [id, entry] of tiles) if (entry.model) loadedSet.add(id);

    const decision = scheduleTiles(
      tileset.root,
      {
        eyeEnu,
        frustumPlanes: enuPlanes,
        viewportHeightPx: canvasH,
        fovRad: 0.6435, // MapLibre 缺省 fov（36.87°）
      },
      mounted.lodPolicy,
      loadedSet,
      mounted.prevRender,
    );
    mounted.prevRender = decision.render;

    for (const id of decision.unload) {
      const entry = tiles.get(id);
      if (!entry || !entry.model) continue;
      mounted.group.remove(entry.model.root);
      entry.model.dispose();
      entry.model = null;
      if (!entry.loading) tiles.delete(id);
    }
    this.evictTileLru(mounted);

    const maxLoads = this.options.maxConcurrentTileLoads ?? 4;
    for (const id of decision.load) {
      if (mounted.loadInFlight >= maxLoads) break;
      const entry = tiles.get(id);
      if (!entry || entry.loading) continue;
      void this.loadTile(mounted, entry);
    }
    this.map.triggerRepaint?.();
  }

  private async loadTile(mounted: MountedAsset, entry: TileEntry): Promise<void> {
    if (!entry.tile.contentUri || entry.loading) return;
    entry.loading = true;
    mounted.loadInFlight += 1;
    const generation = mounted.generation;
    try {
      const buffer = await fetchBinary(entry.tile.contentUri, {
        signal: mounted.controller.signal,
      });
      if (mounted.generation !== generation) return;
      const parsed = await parseGltf(buffer);
      if (mounted.generation !== generation) return;
      const built = buildModel(parsed);
      entry.model = built;
      built.root.matrix.fromArray(entry.tile.localToEnu);
      built.root.matrixAutoUpdate = false;
      built.root.matrixWorldNeedsUpdate = true;
      built.root.traverse((o) => {
        if ((o as THREE.Mesh).isMesh) {
          (o.userData as Record<string, unknown>).scene3dTileId = entry.tile.id;
          (o.userData as Record<string, unknown>).scene3dTileExtras = entry.tile.extras;
        }
      });
      mounted.group.add(built.root);
      entry.lastUsed = Date.now();
      this.map.triggerRepaint?.();
      this.scheduleAsset(mounted, true); // 渐进细化再调度
    } catch {
      if (mounted.generation === generation && !mounted.controller.signal.aborted) {
        recordSceneAssetStatus(mounted.assetId, 'degraded', `tile_load_failed:${entry.tile.id}`);
      }
    } finally {
      entry.loading = false;
      mounted.loadInFlight = Math.max(0, mounted.loadInFlight - 1);
    }
  }

  private evictTileLru(mounted: MountedAsset): void {
    const cap = this.options.tileCacheSize ?? 96;
    const tiles = mounted.tiles;
    if (!tiles) return;
    const cached = [...tiles.values()].filter((t) => t.model).sort((a, b) => a.lastUsed - b.lastUsed);
    let excess = cached.length - cap;
    for (const entry of cached) {
      if (excess <= 0) break;
      if (mounted.prevRender.has(entry.tile.id)) continue;
      entry.model?.dispose();
      if (entry.model) mounted.group.remove(entry.model.root);
      entry.model = null;
      tiles.delete(entry.tile.id);
      excess -= 1;
    }
  }

  private canvasHeight(): number {
    const el = this.map.getCanvasContainer?.() ?? this.map.getContainer?.();
    return el?.clientHeight ?? 800;
  }

  /**
   * 眼位反解：取两条 NDC 像素射线（中心 + 边缘）的最近点 —— 透视射线
   * 共源於眼，最近点即眼位（maplibre 相机模型：透视投影）。
   */
  private eyeFromMvp(mvp: Mat4 | null): WorldCoord | null {
    if (!mvp) return null;
    const inv = invertMat4(mvp);
    if (!inv) return null;
    const rayA = rayThroughNdc(inv, 0, 0);
    const rayB = rayThroughNdc(inv, 1, 0.5);
    if (!rayA || !rayB) return null;
    const p = closestPointBetweenLines(rayA, rayB);
    return p ?? rayA.origin;
  }

  private rayFromPixel(
    px: number,
    py: number,
    canvasW: number,
    canvasH: number,
  ): { origin: WorldCoord; direction: WorldCoord } | null {
    const mvp = this.latestMvp();
    if (!mvp) return null;
    const inv = invertMat4(mvp);
    if (!inv) return null;
    const ndcX = (px / Math.max(canvasW, 1)) * 2 - 1;
    const ndcY = 1 - (py / Math.max(canvasH, 1)) * 2;
    return rayThroughNdc(inv, ndcX, ndcY);
  }

  private latestMvp(): Mat4 | null {
    for (const asset of this.mounted.values()) {
      if (asset.lastMvp) return asset.lastMvp;
    }
    return null;
  }
}

// ── 纯工具 ───────────────────────────────────────────────────────────────

function rayThroughNdc(inv: Mat4, ndcX: number, ndcY: number): { origin: WorldCoord; direction: WorldCoord } | null {
  const unproject = (z: number): WorldCoord | null => {
    const e = applyMat4Point(inv, ndcX, ndcY, z);
    if (!Number.isFinite(e[3]) || e[3] === 0) return null;
    return { x: e[0] / e[3], y: e[1] / e[3], z: e[2] / e[3] };
  };
  const near = unproject(-1);
  const far = unproject(1);
  if (!near || !far) return null;
  const dx = far.x - near.x;
  const dy = far.y - near.y;
  const dz = far.z - near.z;
  const len = Math.hypot(dx, dy, dz);
  if (len === 0 || !Number.isFinite(len)) return null;
  return { origin: near, direction: { x: dx / len, y: dy / len, z: dz / len } };
}

/** 两直线最近点（a 为结果点；两射线同源时最近点 = 眼）。 */
function closestPointBetweenLines(
  a: { origin: WorldCoord; direction: WorldCoord },
  b: { origin: WorldCoord; direction: WorldCoord },
): WorldCoord | null {
  const w0 = {
    x: a.origin.x - b.origin.x,
    y: a.origin.y - b.origin.y,
    z: a.origin.z - b.origin.z,
  };
  const u = a.direction;
  const v = b.direction;
  const a01 = u.x * v.x + u.y * v.y + u.z * v.z;
  const b00 = v.x * v.x + v.y * v.y + v.z * v.z;
  const c00 = w0.x * v.x + w0.y * v.y + w0.z * v.z;
  const denom = 1 - a01 * a01 / (b00 || 1);
  if (Math.abs(denom) < 1e-9) return null;
  const s = (a01 * c00 / (b00 || 1) - (w0.x * u.x + w0.y * u.y + w0.z * u.z)) / denom;
  return {
    x: a.origin.x + u.x * s,
    y: a.origin.y + u.y * s,
    z: a.origin.z + u.z * s,
  };
}

function applyMat4Point(m: Mat4, x: number, y: number, z: number): [number, number, number, number] {
  return [
    m[0] * x + m[4] * y + m[8] * z + m[12],
    m[1] * x + m[5] * y + m[9] * z + m[13],
    m[2] * x + m[6] * y + m[10] * z + m[14],
    m[3] * x + m[7] * y + m[11] * z + m[15],
  ];
}

function transformPoint(m: Mat4, p: { x: number; y: number; z: number }): WorldCoord {
  const e = applyMat4Point(m, p.x, p.y, p.z);
  const w = e[3] || 1;
  return { x: e[0] / w, y: e[1] / w, z: e[2] / w };
}

function worldToEnu(placement: Mat4, world: WorldCoord): WorldCoord {
  const inv = invertMat4(placement);
  if (!inv) return { x: 0, y: 0, z: 0 };
  return transformPoint(inv, world);
}

function collectTiles(tile: TileNode, into: Map<string, TileEntry>): void {
  if (tile.contentUri) {
    into.set(tile.id, { tile, model: null, loading: false, lastUsed: 0 });
  }
  for (const c of tile.children) collectTiles(c, into);
}

function matEquals(a: Mat4, b: Mat4): boolean {
  for (let i = 0; i < 16; i++) if (a[i] !== b[i]) return false;
  return true;
}

function resolveUriSafe(base: string, uri: string): string | null {
  try {
    const resolved = new URL(uri, base);
    if (resolved.protocol !== 'https:' && resolved.origin !== (typeof location !== 'undefined' ? location.origin : '')) {
      return null;
    }
    return resolved.toString();
  } catch {
    return null;
  }
}
