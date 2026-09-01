import type { Map as MaplibreMap } from "maplibre-gl";
import { diffSpecs, type SpecPatch } from "@/lib/mapspec-compiler/reconciler";
import { diffSpecsAsync, disposeWorker, consumeDiffLastFailed } from "@/lib/mapspec-compiler/worker-bridge";
import type { MapSpec, MapSpecSource, MapSpecLayer } from "@/lib/mapspec-compiler/types";
import { toMapLibrePaint } from "@/lib/mapspec-runtime/paint-bridge";
import { isRefOnlySource } from "@/lib/mapspec/ref-source-resolver";
import { RenderDebouncer, type RenderOperation } from "@/lib/map-kit/render-debouncer";
import * as renderer from "@/lib/map-kit/renderer";
import { recordDebounceFrame } from "@/lib/utils/perf-counters";
import { devOnly } from "@/lib/utils/logger";

/**
 * MapSpecRuntime — the deep module that reconciles a declarative MapSpec
 * against a live MapLibre map instance (ADR-0036).
 *
 *   const rt = new MapSpecRuntime(map)
 *   rt.reconcile(spec)        // diff vs last-applied → minimal patch
 *   rt.getAppliedSpec()       // what the map currently reflects
 *   rt.dispose()              // tear down timers + refs
 *
 * Responsibilities:
 *  - Owns the style-loaded retry loop (was 3 refs in map-panel.tsx).
 *  - Applies the SpecPatch: add/remove/update sources & layers.
 *  - Reuses renderer.addGeoJsonSource/addImageSource/addRasterTileSource so the
 *    F28 (image cache-buster) and F31 (geojson ref-cache) optimizations survive.
 *  - Re-syncs z-order via renderer.syncLayerZOrder after every apply.
 *
 * Deliberately does NOT:
 *  - Apply view changes (Q3 — view stays imperative via flyTo/easeTo in callers).
 *  - Manage interactiveLayerIds / click handling (those stay in map-panel.tsx).
 */

const STYLE_RETRY_MS = 100;
export const MAX_STYLE_RETRY_ATTEMPTS = 50;

interface MapSpecRuntimeOptions {
  onStyleRecovery?: () => void;
}

export class MapSpecRuntime {
  /**
   * #1009 类型边界：runtime 持有真实 MapLibre Map 类型（此前 any——字段
   * 契约变更只能在运行时暴露）。测试的结构化 mock 经构造函数边界收敛
   * （调用侧 ``as unknown as MaplibreMap``），核心逻辑不再依赖 any。
   */
  private map: MaplibreMap;
  private appliedSpec: MapSpec | null = null;
  private pendingTimer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
  private debouncer: RenderDebouncer | null;
  // Serialize async reconciles: each request is chained after the previous
  // one COMPLETES (i.e. after its ops have actually executed on the map).
  // This keeps the diff basis (`appliedSpec`) equal to the map's real state —
  // see the appliedSpec note in applyPatchDebounced.
  private reconcileTail: Promise<void> = Promise.resolve();
  // #1078(G-5): 入队合并 —— 尚未开始 diff 的排队请求被更新的 spec 直接
  // 替换（被超越的中间 spec 永远不需要 diff：应用它再应用后续等价于直接
  // 应用后续）。一次 step_result 典型触发 2-3 次 reconcile effect，旧实现
  // 链式逐个全量 worker diff。
  private queuedSpec: MapSpec | null = null;
  private queuedPromise: Promise<void> | null = null;
  private applySeq = 0;
  private currentApplyResolve: (() => void) | null = null;
  private lastError: string | null = null;
  /**
   * FIX-3-9 (#375): the last layer ORDER (ids joined) that syncLayerZOrder was
   * invoked for. diffSpecs compares layers by id and never notices a pure
   * reordering (empty layer patch), so `patch.layers.length > 0` alone cannot
   * gate the z-order re-sync — a reorder must too. Comparing this cheap joined
   * key against the next spec's order costs one string build per apply and
   * stays silent for the common no-op/unchanged-order reconciles (fa108d3's
   * work-count contract: no extra MapLibre work when nothing reordered).
   */
  private lastLayerOrderKey: string | null = null;
  private styleRecoveryHandler: (() => void) | null = null;
  private pendingRecoverySpec: MapSpec | null = null;
  private readonly onStyleRecovery?: () => void;
  /**
   * #459: style generation token. Bumped by invalidateStyle() (basemap
   * setStyle). A debounced patch captures the token at ENQUEUE time and its
   * z-order completion marker may only advance `appliedSpec` when the token is
   * unchanged — a patch that started before the wipe must never claim the map
   * reflects its spec, or the recovery reconcile diffs against a resurrected
   * stale basis and emits an empty (heal-nothing) patch.
   */
  private styleEpoch = 0;
  /**
   * #462: layerId → sourceId for every layer this runtime added. MapLibre
   * offers no "layers of source" query without cloning the whole style via
   * getStyle(); the index answers removeSourceSafe's straggler scrub with
   * plain Map lookups instead. Maintained by addLayerSafe/removeLayerSafe and
   * cleared alongside appliedSpec on a style wipe.
   */
  private layerSourceIndex = new Map<string, string>();

  constructor(map: MaplibreMap, options: MapSpecRuntimeOptions = {}) {
    this.map = map;
    this.onStyleRecovery = options.onStyleRecovery;
    // FE-3: wire the debouncer's FrameStats instrument to the dev/test counter
    // sink (was constructed with no options — findings E5).
    this.debouncer = new RenderDebouncer(map, {
      onFrameStats: (stats) => recordDebounceFrame(stats),
    });
  }

  /**
   * Invalidate the last-applied spec state (e.g. when base style changes).
   * Next reconcile will treat all sources/layers as new and re-apply them.
   */
  invalidateStyle(): void {
    // #459: invalidate any patch whose ops are still queued/running — its
    // completion marker checks this token before advancing appliedSpec.
    this.styleEpoch++;
    this.appliedSpec = null;
    // #462: setStyle dropped every layer without passing through any removal
    // site — drop the runtime's layer→source index and the renderer's
    // layer-id order registry for this map (next read re-seeds cold).
    this.layerSourceIndex.clear();
    renderer.clearStyleLayerIds(this.map);
    // FE-P3-5: a base-style change wipes every source WITHOUT going through
    // removeSourceSafe — prune the inline-GeoJSON registry so the viewport
    // refresher stops probing ids that no longer exist (never converged).
    renderer.unregisterAllGeoJsonSources();
  }

  /**
   * Diff `nextSpec` against the last-applied spec and apply the minimal patch.
   * If the map style isn't loaded yet, schedules a retry (owning the loop that
   * was previously 3 React refs in map-panel.tsx).
   */
  reconcile(nextSpec: MapSpec, retryAttempt = 0): void {
    if (this.disposed || !this.map) return;
    if (this.appliedSpec === nextSpec) return;

    // Defer until the base style is loaded. This mirrors map-panel.tsx:167-170
    // but the retry state lives here, not in React refs.
    if (!this.map.isStyleLoaded()) {
      if (retryAttempt >= MAX_STYLE_RETRY_ATTEMPTS) {
        this.lastError = "style_load_timeout";
        this.armStyleRecovery(nextSpec);
        return;
      }
      if (this.pendingTimer) clearTimeout(this.pendingTimer);
      this.pendingTimer = setTimeout(() => {
        this.pendingTimer = null;
        this.reconcile(nextSpec, retryAttempt + 1);
      }, STYLE_RETRY_MS);
      return;
    }

    this.clearStyleRecovery();
    this.lastError = null;
    const patch = diffSpecs(this.appliedSpec, nextSpec);
    this.applyPatchDirect(patch, nextSpec);
  }

  /**
   * Async reconcile — worker-offloaded diff + frame-budgeted application.
   *
   * Same contract as `reconcile` (diff against last-applied → minimal patch),
   * but: the diff runs in a Web Worker when available (falling back to the
   * main thread), and the patch is applied through a RenderDebouncer so
   * MapLibre mutations are coalesced and time-sliced per rAF frame instead of
   * blocking the UI thread (ADR-0036 / issue #227).
   *
   * Requests are SERIALIZED (each chained after the previous one's ops have
   * actually executed). This is what makes the diff basis always equal the
   * map's real state: a rapid specB arriving before specA's ops ran must not
   * diff against a specA that the map never reflected (see applyPatchDebounced).
   *
   * Fire-and-forget from callers: `void rt.reconcileAsync(spec)`.
   * `flush()` drains pending operations synchronously (tests / screenshots).
   * The returned promise resolves once THIS request's ops have executed.
   */
  reconcileAsync(nextSpec: MapSpec): Promise<void> {
    if (this.disposed || !this.map) return Promise.resolve();
    // #1078(G-5): 等价门 —— composeLiveMapSpec 对输入未变的重复 compose
    // 返回同一对象（调用方 memo），对象身份即可判等（与同步路径同款），
    // 主线程不付内容哈希。
    if (this.appliedSpec === nextSpec) return Promise.resolve();
    // 入队合并：尚未开始 diff 的排队请求被更新的 spec 直接替换（被超越的
    // 中间 spec 不需要 diff —— 应用它再应用后续等价于直接应用后续）。
    // 所有等待者共享同一 promise。
    if (this.queuedPromise) {
      this.queuedSpec = nextSpec;
      return this.queuedPromise;
    }
    const promise = this.reconcileTail
      .then(() => {
        const spec = this.queuedSpec ?? nextSpec;
        this.queuedSpec = null;
        this.queuedPromise = null;
        return this.processOne(spec, 0);
      })
      .catch((err) => {
        // #692：链内兜底 catch 仅供断链保护——生产静默（此前裸 console.warn
        // 是生产噪声），dev 下保留可诊断性。map-panel 侧挂在该链尾部的
        // .catch 因此可达性不变（本 catch 吞掉后链恢复 resolved）。
        // #1008：手工 NODE_ENV 门禁统一收敛到 devOnly（同文件一致）。
        devOnly.warn("[MapSpecRuntime] reconcileAsync error:", err);
      });
    this.queuedSpec = nextSpec;
    this.queuedPromise = promise;
    this.reconcileTail = promise;
    return promise;
  }

  private async processOne(nextSpec: MapSpec, retryAttempt: number): Promise<void> {
    if (this.disposed || !this.map) return;

    // Defer until the base style is loaded (mirrors the sync retry loop).
    if (!this.map.isStyleLoaded()) {
      if (retryAttempt >= MAX_STYLE_RETRY_ATTEMPTS) {
        this.lastError = "style_load_timeout";
        this.armStyleRecovery(nextSpec);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, STYLE_RETRY_MS));
      if (this.disposed || !this.map) return;
      return this.processOne(nextSpec, retryAttempt + 1);
    }

    this.clearStyleRecovery();
    this.lastError = null;
    const patch = await diffSpecsAsync(this.appliedSpec, nextSpec);
    if (this.disposed || !this.map) return;
    // Resolves when the patch's ops have actually run (last op = z-order).
    await this.applyPatchDebounced(patch, nextSpec);
  }

  /**
   * Synchronously drain all pending debounced operations (e.g. before unmount
   * or taking a screenshot). No-op when nothing is queued.
   */
  flush(): void {
    this.debouncer?.flush();
  }

  /**
   * Apply a SpecPatch immediately (synchronous path). Kept as the
   * correctness-preserving reference used by `reconcile()` and the sync
   * test suite; the debounced path enqueues the same operations in the same
   * strict order.
   */
  private applyPatchDirect(patch: SpecPatch, nextSpec: MapSpec): void {
    // --- layers (remove + recompile may free sources) ---
    for (const change of patch.layers) {
      if (change.kind === "remove") {
        this.removeLayerSafe(change.id);
      } else if (change.kind === "recompile") {
        // Q3 fallback: remove + re-add rather than diffing individual paint props.
        this.removeLayerSafe(change.id);
      }
      // `add` is handled below, after sources are ready.
      // `filter` (Runtime V4 fast path) is applied below via setFilter — no
      // remove/add churn, source untouched, z-order untouched.
    }

    // --- sources ---
    for (const change of patch.sources) {
      if (change.kind === "remove") {
        this.removeSourceSafe(change.id);
      } else if ((change.kind === "add" || change.kind === "update") && change.next) {
        // add/update both route through the idempotent renderer helpers (they
        // carry the F28/F31 cache logic). Tile-URL sources carry no cache state
        // and addRasterTileSource is itself idempotent.
        this.applySource(change.id, change.next, change.kind === "update");
      }
    }

    // --- layers (add + recompile re-add) ---
    for (const change of patch.layers) {
      if ((change.kind === "add" || change.kind === "recompile") && change.next) {
        this.addLayerSafe(change.next);
      }
    }

    // --- filters (V4 fast path): setFilter only, after layers exist ---
    for (const change of patch.layers) {
      if (change.kind === "filter" && change.next) {
        this.applyFilterSafe(change.id, change.next.filter);
      }
    }

    // --- z-order: re-sync when layers were modified OR the order changed ---
    // The order half is #375: diffSpecs never reports a pure reordering (empty
    // layer patch), so without this gate the drag-reorder in the Layers tab was
    // a silent visual no-op. The joined-key comparison is cheap and skips
    // unchanged orders, preserving fa108d3's no-op work-count contract.
    const orderedIds = nextSpec.layers.map((l) => l.id);
    const orderKey = orderedIds.join("\u0000");
    // V4 review：filter-only 变化不改结构/顺序 —— 不重跑全量 z 同步
    // （选择翻转是最高频路径；z-order 幂等但白费）。
    if (this.hasStructuralLayerChange(patch) || orderKey !== this.lastLayerOrderKey) {
      renderer.syncLayerZOrder(this.map, "", orderedIds);
      this.lastLayerOrderKey = orderKey;
    }

    if (!this.lastError) this.appliedSpec = nextSpec;
  }

  /** 结构性层变化 = 非 filter-only 的任一变化（add/remove/recompile）。 */
  private hasStructuralLayerChange(patch: SpecPatch): boolean {
    return patch.layers.some((c) => c.kind !== "filter");
  }

  /**
   * Enqueue a SpecPatch as coalesced, frame-budgeted render operations.
   * Ordering mirrors applyPatchDirect exactly (layers-remove → sources →
   * layers-add → z-order), all high priority so the sequence within a frame
   * is preserved — MapLibre requires a layer's source to exist first.
   *
   * `appliedSpec` is updated only when the LAST op of this patch has actually
   * executed (the z-order op, always enqueued last with a UNIQUE id so the
   * debouncer never coalesces two patches' completion markers). Updating it at
   * enqueue time was a correctness bug: the debouncer coalesces ops by id, so
   * a rapid second reconcile could merge `source:apply:S` and drop the first
   * patch's layer add — while appliedSpec already claimed the map reflected
   * the second spec. The diff basis then permanently diverged from the map.
   *
   * Resolves once the patch's ops have run (or immediately if disposed).
   */
  private applyPatchDebounced(patch: SpecPatch, nextSpec: MapSpec): Promise<void> {
    // FIX-3-6: a worker error/timeout resolves as the EMPTY patch, which is
    // content-identical to a genuine no-op diff — but a failed diff is NOT a
    // proof of equality, so the map never received `nextSpec`'s layers.
    // Advancing appliedSpec here would make interactive ids claim layers that
    // don't exist (the runtime's id registry derives from appliedSpec). When
    // the worker-bridge flags a failure, skip advancement, clear the flag and
    // resolve so the serialized reconcile chain keeps flowing (the caller's
    // style-scan fallback then yields correct ids from the live map).
    if (
      patch.sources.length === 0 &&
      patch.layers.length === 0 &&
      consumeDiffLastFailed()
    ) {
      return Promise.resolve();
    }

    const ops: RenderOperation[] = [];

    // 数据 ref 尚未解析的源（后端直写 MapSpec 的 {ref_id} 源，payload 由
    // ref-source-resolver / HUD 异步补齐）此刻无法上地图：跳过其源应用与
    // 依赖图层的 add/recompile，不触发 MapLibre 的 "source not found" 报错，
    // 也不污染 lastError（#459：lastError 会阻断 appliedSpec 前进）。数据
    // 到达后的下一次 diff 会以 source:update + layer:recompile 补挂载。
    const pendingRefSources = new Set<string>();
    for (const layer of nextSpec.layers) {
      if (layer.source && isRefOnlySource(nextSpec.sources?.[layer.source])) {
        pendingRefSources.add(layer.source);
      }
    }

    for (const change of patch.layers) {
      if (change.kind === "remove" || change.kind === "recompile") {
        ops.push({
          id: `layer:remove:${change.id}`,
          type: "REMOVE_LAYER",
          priority: "high",
          execute: () => this.removeLayerSafe(change.id),
        });
      }
    }

    for (const change of patch.sources) {
      if (change.kind === "remove") {
        ops.push({
          id: `source:remove:${change.id}`,
          type: "REMOVE_LAYER",
          priority: "high",
          execute: () => this.removeSourceSafe(change.id),
        });
      } else if ((change.kind === "add" || change.kind === "update") && change.next) {
        if (pendingRefSources.has(change.id)) continue;
        const next = change.next;
        ops.push({
          id: `source:apply:${change.id}`,
          type: "UPDATE_GEOJSON",
          priority: "high",
          execute: () => this.applySource(change.id, next, change.kind === "update"),
        });
      }
    }

    for (const change of patch.layers) {
      if ((change.kind === "add" || change.kind === "recompile") && change.next) {
        if (pendingRefSources.has(change.next.source)) continue;
        const next = change.next;
        ops.push({
          id: `layer:add:${change.id}`,
          type: "ADD_LAYER",
          priority: "high",
          execute: () => this.addLayerSafe(next),
        });
      }
    }

    // V4 filter fast path: setFilter after add/recompile ops are enqueued
    // (same-frame ordering preserved — all high priority, FIFO within frame).
    // Op id reuses `layer:add:${id}` discipline but with its own prefix so a
    // rapid selection flip coalesces by layer (only the latest filter runs).
    for (const change of patch.layers) {
      if (change.kind === "filter" && change.next) {
        const nextFilter = change.next.filter;
        ops.push({
          id: `layer:filter:${change.id}`,
          type: "UPDATE_GEOJSON",
          priority: "high",
          execute: () => this.applyFilterSafe(change.id, nextFilter),
        });
      }
    }

    const orderedIds = nextSpec.layers.map((l) => l.id);
    // #375: same order-key gate as applyPatchDirect — computed at enqueue time,
    // compared against the last EXECUTED order at op-run time (queued patches
    // run sequentially, so each comparison sees the previous patch's outcome).
    const orderKey = orderedIds.join("\u0000");
    // #459: generation token captured at enqueue. setStyle → invalidateStyle()
    // bumps it; a patch whose ops execute after a wipe must not be recorded as
    // applied (the map no longer reflects whatever subset of it had run).
    const epochAtEnqueue = this.styleEpoch;
    const zorderId = `zorder:sync:${++this.applySeq}`; // unique per patch — never coalesced
    ops.push({
      id: zorderId,
      type: "SET_STYLE",
      priority: "high",
      execute: () => {
        if (this.hasStructuralLayerChange(patch) || orderKey !== this.lastLayerOrderKey) {
          renderer.syncLayerZOrder(this.map, "", orderedIds);
          this.lastLayerOrderKey = orderKey;
        }
        // All ops of this patch have now run (z-order is enqueued last in the
        // high-priority FIFO). appliedSpec may now legitimately equal the map —
        // UNLESS the style was invalidated since enqueue (#459): the patch ran
        // across a setStyle wipe, so claiming it applied would resurrect a
        // pre-wipe basis and the recovery reconcile would diff to an empty
        // patch, leaving wiped layers gone forever.
        if (!this.lastError && epochAtEnqueue === this.styleEpoch) {
          this.appliedSpec = nextSpec;
        }
        const resolve = this.currentApplyResolve;
        this.currentApplyResolve = null;
        if (resolve) resolve();
      },
    });

    return new Promise<void>((resolve) => {
      this.currentApplyResolve = resolve;
      for (const op of ops) {
        this.debouncer?.enqueue(op);
      }
      // No debouncer (already disposed) → ops never run; release immediately.
      if (!this.debouncer) {
        this.currentApplyResolve = null;
        resolve();
      }
    });
  }

  getAppliedSpec(): MapSpec | null {
    return this.appliedSpec;
  }

  /** Last bounded reconciliation failure, for structured runtime evidence. */
  getLastError(): string | null {
    return this.lastError;
  }

  /**
   * True while a debounced patch's ops are enqueued but not all executed yet.
   * During that window the map may be in a partially-patched state that
   * `appliedSpec` cannot describe (appliedSpec advances only on the final
   * z-order op). Interactive-id derivation (FE-3) falls back to scanning the
   * live style while this is true.
   */
  isPatchInFlight(): boolean {
    return this.currentApplyResolve !== null;
  }

  dispose(): void {
    this.disposed = true;
    if (this.pendingTimer) {
      clearTimeout(this.pendingTimer);
      this.pendingTimer = null;
    }
    this.clearStyleRecovery();
    this.debouncer?.dispose();
    this.debouncer = null;
    // FE-01: the worker-bridge keeps its module worker warm for
    // DIFF_WORKER_IDLE_MS after the last diff. When the runtime (and thus the
    // map) is being torn down, tear the warm worker down immediately rather
    // than letting it idle until the timeout.
    disposeWorker();
    // Release any in-flight apply: its ops were just dropped from the queue,
    // so the z-order completion marker will never run.
    const resolve = this.currentApplyResolve;
    this.currentApplyResolve = null;
    if (resolve) resolve();
    // dispose 后 runtime 不可再用（disposed 标志守卫全部入口）；置空断开
    // Map 强引用防泄漏——类型边界处显式断言（#1009）。
    this.map = null as unknown as MaplibreMap;
    this.appliedSpec = null;
  }

  // ---- source/layer application helpers ----

  private armStyleRecovery(nextSpec: MapSpec): void {
    this.pendingRecoverySpec = nextSpec;
    if (
      this.styleRecoveryHandler
      || typeof this.map?.on !== "function"
      || typeof this.map?.off !== "function"
    ) return;
    this.styleRecoveryHandler = () => {
      if (this.disposed || !this.map?.isStyleLoaded?.()) return;
      const pending = this.pendingRecoverySpec;
      this.clearStyleRecovery();
      if (!pending) return;
      this.reconcile(pending);
      this.onStyleRecovery?.();
    };
    this.map.on("styledata", this.styleRecoveryHandler);
  }

  private clearStyleRecovery(): void {
    if (this.styleRecoveryHandler && typeof this.map?.off === "function") {
      this.map.off("styledata", this.styleRecoveryHandler);
    }
    this.styleRecoveryHandler = null;
    this.pendingRecoverySpec = null;
  }

  /**
   * Current map viewport as [west, south, east, north], or undefined when the
   * map has no bounds yet (style not loaded / stub map without getBounds).
   */
  private currentViewport(): import("@/lib/map-kit/renderer").ViewportBBox | undefined {
    const b = this.map?.getBounds?.();
    if (!b || typeof b.getWest !== "function") return undefined;
    return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  }

  /**
   * Translate a MapSpecSource into the MapLibre call. Reuses the existing
   * renderer helpers so F28 (image cache-buster) and F31 (geojson ref-cache)
   * optimizations are preserved exactly.
   */
  private applySource(id: string, source: MapSpecSource, replaceExisting = false): void {
    const existingSource = this.map.getSource(id) as { type?: string } | undefined;
    if (existingSource && (replaceExisting || existingSource.type !== source.type)) {
      // The reconciler has already scheduled every dependent layer for
      // recompile. Replace the definition instead of relying on renderer
      // helpers whose same-type fast paths deliberately no-op tile/image URL
      // changes.
      this.removeSourceSafe(id);
      if (this.map.getSource(id)) {
        this.lastError = `replace_source_failed:${id}`;
        return;
      }
    }
    if (source.type === "raster") {
      // Raster image source (HeatmapRasterSource path).
      renderer.addImageSource(
        this.map,
        id,
        source.imageRef,
        boundsToImageCorners(source.bounds),
      );
    } else if (source.type === "vector") {
      // Data Plane: MVT 矢量瓦片源（大 POI 图层显示路径）。
      renderer.addVectorTileSource(this.map, id, source.tiles, source.minzoom, source.maxzoom);
    } else if (source.inlineData) {
      // Phase 8: pass the current viewport so large inline FeatureCollections
      // are trimmed to the visible area at apply time (further re-filtering
      // happens on move via map-panel's refreshGeoJsonSourcesByViewport).
      renderer.addGeoJsonSource(this.map, id, source.inlineData, {
        viewport: this.currentViewport(),
      });
    } else {
      const tileUrl = source.url || source.dataPath;
      if (tileUrl) {
        // Tile URL source (raster/tile layer). The adapter emits this shape for
        // string-sourced layers. addRasterTileSource is idempotent (no-op if exists).
        renderer.addRasterTileSource(this.map, id, tileUrl);
      }
    }
    // else: nothing to apply (empty fallback source already added? skip).
  }

  private addLayerSafe(layer: MapSpecLayer): void {
    // MapSpecLayer is already shaped close to a MapLibre layer spec. We pass it
    // through with only the fields MapLibre expects. Paint goes through the
    // dialect bridge: backend-authored layers carry canonical short keys
    // (`color`/`radius`/…) while the adapter emits MapLibre-native keys —
    // MapLibre rejects the former as unknown properties.
    const def: any = {
      id: layer.id,
      type: layer.type,
      source: layer.source,
      paint: toMapLibrePaint(layer),
      layout: layer.layout || {},
    };
    if (layer.filter) def.filter = layer.filter;
    // Data Plane: 矢量瓦片源要求 source-layer 字段（编码器固定用 "data"）。
    const src = this.map.getSource(layer.source);
    if (src && (src as any).type === "vector") {
      def["source-layer"] = "data";
    }
    // Some layer types carry maxzoom (native heatmap). MapSpecLayer doesn't
    // model that today; if needed, extend the type. For now omit.
    try {
      // #459: idempotent add. A stale in-flight patch can re-add this very
      // layer onto the freshly-wiped style moments before the recovery patch's
      // full re-apply reaches it — MapLibre throws on a duplicate id, which
      // used to poison `lastError` and block the recovery's appliedSpec
      // advancement. Drop any survivor first so the final definition is
      // exactly the spec's.
      if (this.map.getLayer(layer.id)) {
        this.removeLayerSafe(layer.id);
      }
      this.map.addLayer(def);
      // #462: keep the layer→source index + renderer id-order registry exact.
      this.layerSourceIndex.set(layer.id, layer.source);
      renderer.noteStyleLayerAdded(this.map, layer.id);
      // v2(audit FE2)：spec 层 label 上活地图 —— headless compiler 一直为
      // layer.label 生成 `${id}-label` symbol 子层，活路径从未挂载（导出
      // 与屏幕内容漂移）。方言与编译器对齐：label 是 MapSpecLayerLabel
      // 对象（field/size/color/halo*）或 layout.labelField 标量回退。
      const labelSpecRaw = (layer as any).label;
      const labelSpec = (
        (labelSpecRaw && typeof labelSpecRaw === "object" && labelSpecRaw.field)
          ? labelSpecRaw
          : (layer.layout?.labelField ? { field: layer.layout.labelField } : undefined)
      );
      if (labelSpec?.field && layer.type !== "raster" && layer.type !== "heatmap") {
        this.addLabelSublayerSafe(layer, labelSpec);
      }
    } catch (err) {
      // Defensive: a recompile that races with a style swap may find the layer
      // already re-added by the styledata path. Log and continue rather than
      // throwing the whole reconcile.
       
      // #1008：addLayer 失败的裸 console.warn（泄漏内部层 id）→ devOnly，
      // 与同文件 reconcileAsync 的门禁一致。
      devOnly.warn(`[MapSpecRuntime] addLayer failed for ${layer.id}:`, err);
      this.lastError = `add_layer_failed:${layer.id}`;
    }
  }

  private removeLayerSafe(id: string): void {
    if (this.map.getLayer(id)) {
      try { this.map.removeLayer(id); } catch { /* already gone */ }
    }
    this.layerSourceIndex.delete(id);
    renderer.noteStyleLayerRemoved(this.map, id);
    // FE2：label 子层（`${id}-label`）与主层同生命周期 —— 删主层必须
    // 一并删除，否则留下无 source 消费者的 ghost 文本层。
    const labelId = `${id}-label`;
    if (this.map.getLayer(labelId)) {
      try { this.map.removeLayer(labelId); } catch { /* already gone */ }
      renderer.noteStyleLayerRemoved(this.map, labelId);
    }
  }

  /**
   * V4 filter fast path: apply a filter-only layer change via map.setFilter.
   *
   * Semantics: absence of the layer on the map is a NO-OP (not lastError) —
   * a spec layer whose source is still a pending `{ref_id}` placeholder was
   * deliberately never mounted; when its data lands the diff re-detects the
   * layer as add/recompile with the final filter. Genuine runtime divergence
   * (layer lost) belongs to the render-observation/repair domain.
   */
  private applyFilterSafe(id: string, filter: unknown[] | undefined): void {
    if (!this.map.getLayer(id)) {
      devOnly.warn(`[MapSpecRuntime] setFilter skipped, layer absent: ${id}`);
      return;
    }
    try {
      this.map.setFilter(id, (filter ?? null) as any);
      // V4 review：label 子层（`${id}-label`）与主层同生命周期 —— 主层
      // 过滤翻转时同步应用，否则被滤要素的注记残留在画布上。
      const labelId = `${id}-label`;
      if (this.map.getLayer(labelId)) {
        this.map.setFilter(labelId, (filter ?? null) as any);
      }
    } catch (err) {
      // A structurally invalid expression (style-spec rejection) must surface
      // as bounded runtime evidence, not silently swallow the change.
      devOnly.warn(`[MapSpecRuntime] setFilter failed for ${id}:`, err);
      this.lastError = `set_filter_failed:${id}`;
    }
  }

  /** FE2：spec 层的 label symbol 子层（`${id}-label`，编译器同款方言/样式）。 */
  private addLabelSublayerSafe(
    layer: MapSpecLayer,
    labelSpec: { field: string; size?: unknown; color?: unknown; haloColor?: string; haloWidth?: number },
  ): void {
    const labelId = `${layer.id}-label`;
    const layout = (layer.layout as any) ?? {};
    const def: any = {
      id: labelId,
      type: "symbol",
      source: layer.source,
      paint: {
        // 与 compiler.ts 的默认一致：黑字 + 白晕（任意底图可读，#1007）。
        "text-color": (labelSpec.color as any) ?? layout.labelColor ?? "#000000",
        "text-halo-color": labelSpec.haloColor ?? "#ffffff",
        "text-halo-width": labelSpec.haloWidth ?? 1,
      },
      layout: {
        "text-field": ["get", String(labelSpec.field)],
        "text-size": (labelSpec.size as any) ?? layout.labelSize ?? 12,
        "text-allow-overlap": false,
        visibility: layout.visibility ?? "visible",
      },
    };
    const src = this.map.getSource(layer.source);
    if (src && (src as any).type === "vector") def["source-layer"] = "data";
    try {
      if (this.map.getLayer(labelId)) this.removeLayerSafe(labelId);
      this.map.addLayer(def);
      this.layerSourceIndex.set(labelId, layer.source);
      renderer.noteStyleLayerAdded(this.map, labelId);
    } catch (err) {
      devOnly.warn(`[MapSpecRuntime] label sublayer failed for ${layer.id}:`, err);
    }
  }

  private removeSourceSafe(id: string): void {
    // Must remove all layers referencing this source first. diffSpecs already
    // reported dependent layer removes, but defensive: scrub any stragglers.
    // #462: the runtime's own layer→source index replaces the per-removal
    // getStyle() deep clone (a patch removing N sources cloned the style N
    // times); the index covers every layer this runtime added, which is
    // exactly the population that can reference a spec source.
    for (const [layerId, sourceId] of Array.from(this.layerSourceIndex)) {
      if (sourceId === id) {
        this.removeLayerSafe(layerId);
      }
    }
    if (this.map.getSource(id)) {
      try { this.map.removeSource(id); } catch { /* silent */ }
      renderer.unregisterGeoJsonSource(id);
    }
  }
}

/** WGS84 bounds [w, s, e, n] → MapLibre image corners [TL, TR, BR, BL]. */
function boundsToImageCorners(bounds: [number, number, number, number]): [[number, number], [number, number], [number, number], [number, number]] {
  const [w, s, e, n] = bounds;
  return [
    [w, n],
    [e, n],
    [e, s],
    [w, s],
  ];
}
