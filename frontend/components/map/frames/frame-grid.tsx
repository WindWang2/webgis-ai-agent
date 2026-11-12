'use client';
/**
 * G25 FrameProductGrid —— 多画幅真实交互运行时网格（P1/P2/P3/P5 编排）。
 *
 * 职责（唯一 owner：前端多画幅视图调度与生命周期）：
 * - 从 committed MapSpec 投影产品（G15 projectFrameProduct 复用），经
 *   resolveFrameEffectiveSpec 产出**帧有效文档**（where/time_window 过滤 +
 *   共享分级 —— 与服务端 resolver 逐位一致）；
 * - 单一 live 槽位状态机：占用者 = 活动画幅或快照预热目标；切换前
 *   `capture()` 确定性捕获快照入 LRU store（A10/A11）；活实例 ≤ 预算
 *   （默认 2 计主图；对比视图占用时如实披露，不强行突破 —— A09）；
 * - FrameCameraHub 接线：主图 ↔ 活动画幅联动 + 帧间同步组真实传播 +
 *   未挂载帧期望相机记录（A04–A07）；
 * - 激活流：optimistic 本地 + commitFrameProductPatch(active_frame) CAS
 *   落服务端（刷新恢复 A20；面板经同一服务端真相一致 —— A02/P5）；
 * - 键盘：'[' / ']' 切换活动画幅，Escape 关闭（A25）；scope 徽标明示
 *   「仅本幅」（P5）；
 * - 会话切换 → 快照 store/相机草稿/hub 全部失效重建（A14）。
 *
 * 本组件是 map-panel 的兄弟覆盖层（ComparisonView 同款挂载法，map-panel
 * 零布局改动）。closed 且无产品 → 只渲染入口 chip。
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import type { MapRef } from 'react-map-gl/maplibre';
import type { StyleSpecification } from 'maplibre-gl';
import { Camera, Link2, Link2Off, Loader2, X } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import { registerEscapeHandler } from '@/lib/utils/escape-stack';
import { useHudStore, type HudState } from '@/lib/store/useHudStore';
import { composeLiveMapSpec } from '@/lib/mapspec/live-spec';
import {
  injectResolvedRefSources,
  subscribeRefSources,
  getRefSourcesGeneration,
} from '@/lib/mapspec/ref-source-resolver';
import {
  getCommittedMapSpec,
  getMapSpecLiveGeneration,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { projectFrameProduct, commitFrameProductPatch } from '@/lib/cartography/frame-product';
import {
  cameraWriteGuard,
  checkProjectionCompat,
  projectFrameRuntime,
  selectActiveSuccessor,
  snapshotFingerprint,
  type FrameRuntimeSlotView,
  type MountedFrameFact,
} from '@/lib/cartography/frame-runtime';
import {
  FrameEffectiveSpecError,
  resolveFrameEffectiveSpec,
} from '@/lib/cartography/frame-effective-spec';
import type { CameraSnapshot } from '@/lib/cartography/frame-sync';
import {
  FrameSnapshotStore,
  type FrameSnapshotEntry,
} from '@/lib/cartography/frame-snapshot-store';
import {
  closeFrameGrid,
  getFrameGridSnapshot,
  openFrameGrid,
  setFrameGridSyncDimensions,
  setFrameGridLinkMain,
  subscribeFrameGrid,
  setActiveFrameRuntime,
} from '@/lib/cartography/frame-grid-store';
import { FrameCameraHub } from './frame-camera-hub';
import { FrameViewport, type FrameViewportHandle } from './frame-viewport';
import { mapInstanceRegistry } from './frame-instance-registry';
import { clearFrameObservations, publishFrameObservations } from '@/lib/cartography/frame-observation';
import { devOnly } from '@/lib/utils/logger';

export interface FrameProductGridProps {
  primaryMapRef: React.MutableRefObject<MapRef | null>;
  mapStyle: string | StyleSpecification;
  transformRequest?: (url: string, resourceType?: string) => {
    url: string;
    headers?: Record<string, string>;
  };
  sessionId?: string | null;
  ownerToken?: string | null;
  sessionTokenRef?: React.MutableRefObject<string | null>;
}

/** 相机漂移容差（期望相机 vs 快照捕获相机 —— 快照失效判据）。 */
const CAMERA_DRIFT_EPS = 1e-4;

function draftKey(sessionId: string, frameId: string): string {
  return `g25:cameraDraft:${sessionId}:${frameId}`;
}

export function FrameProductGrid({
  primaryMapRef,
  mapStyle,
  transformRequest,
  sessionId,
  ownerToken,
  sessionTokenRef,
}: FrameProductGridProps) {
  const gridState = useSyncExternalStore(subscribeFrameGrid, getFrameGridSnapshot, getFrameGridSnapshot);
  const committedSpec = useSyncExternalStore(subscribeMapSpecLive, getCommittedMapSpec, getCommittedMapSpec);
  const liveGeneration = useSyncExternalStore(
    subscribeMapSpecLive, getMapSpecLiveGeneration, getMapSpecLiveGeneration,
  );
  const refSourcesGeneration = useSyncExternalStore(
    subscribeRefSources, getRefSourcesGeneration, getRefSourcesGeneration,
  );
  const layers = useHudStore((s: HudState) => s.layers);
  const mapLoaded = useHudStore((s: HudState) => s.mapLoaded ?? false);
  const t = useT('map');

  // ── 运行时状态（观察面，可随时重算）──
  const [mountedFacts, setMountedFacts] = useState(() => new Map<string, MountedFrameFact>());
  const [expectedCameras, setExpectedCameras] = useState(() => new Map<string, CameraSnapshot>());
  const [cameraDrafts, setCameraDrafts] = useState(() => new Map<string, CameraSnapshot>());
  const [liveFrameId, setLiveFrameId] = useState<string | null>(null);
  const [warmupQueue, setWarmupQueue] = useState<string[]>([]);
  const [viewportSize, setViewportSize] = useState<{ width: number; height: number } | null>(null);
  const [registryTick, setRegistryTick] = useState(0);
  const [snapshotTick, setSnapshotTick] = useState(0);

  const snapshotStoreRef = useRef<FrameSnapshotStore | null>(null);
  if (!snapshotStoreRef.current) snapshotStoreRef.current = new FrameSnapshotStore();
  const hubRef = useRef<FrameCameraHub | null>(null);
  const viewportHandleRef = useRef<FrameViewportHandle | null>(null);
  const liveSwitchingRef = useRef<Promise<void> | null>(null);
  const sessionRef = useRef<string | null>(sessionId ?? null);
  const prevActiveRef = useRef<string | null>(null);

  const product = useMemo(() => projectFrameProduct(committedSpec), [committedSpec]);
  const bound = product.bound && product.enabled && product.slots.length > 0;

  // ── 激活（optimistic + CAS 持久化 + live 切换；串行防竞态 A22）。──
  const advanceLiveSlot = useCallback(
    async (next: string | null) => {
      const prior = liveSwitchingRef.current ?? Promise.resolve();
      const run = prior.then(async () => {
        const current = liveFrameIdRef.current;
        if (current === next) return;
        if (current && viewportHandleRef.current) {
          const cap = await viewportHandleRef.current.capture();
          const sid = sessionRef.current ?? '';
          const pv = projectFrameProduct(getCommittedMapSpec());
          const slot = pv.slots.find((s) => s.frameId === current);
          // 快照版本诚实化（评审 M1）：只在「帧仍在产品内 + 无错误事实 +
          // 已落定到 desired revision」时按当前版本入库；错误/loading/部分
          // 渲染的像素不冒充当前版本（宁可无快照，A11/A12 姐妹条款）。
          const fact = mountedFactsRef.current.get(current);
          const settledClean =
            !!slot &&
            !!fact &&
            !fact.errorCode &&
            fact.resolvedSpecRevision === liveGenerationRef.current;
          if (cap && sid && settledClean) {
            const specRev = fact.resolvedSpecRevision;
            const size = { width: cap.width, height: cap.height };
            snapshotStoreRef.current!.put({
              sessionId: sid,
              frameId: current,
              fingerprint: snapshotFingerprint({
                sessionId: sid,
                frameId: current,
                specRevision: specRev,
                dataRevision: slot?.binding?.contentRevision ?? null,
                viewportSize: size,
              }),
              dataUrl: cap.dataUrl,
              bytes: cap.bytes,
              specRevision: specRev,
              dataRevision: slot?.binding?.contentRevision ?? null,
              viewportSize: size,
              capturedAt: Date.now(),
              camera: cap.camera,
            });
          }
        }
        setLiveFrameId(next);
      });
      liveSwitchingRef.current = run;
      try {
        await run;
      } finally {
        liveSwitchingRef.current = null;
      }
    },
    [],
  );

  // 最新值 refs（advanceLiveSlot 不 stale 闭包）。
  const liveFrameIdRef = useRef<string | null>(null);
  liveFrameIdRef.current = liveFrameId;
  const mountedFactsRef = useRef(mountedFacts);
  mountedFactsRef.current = mountedFacts;
  const liveGenerationRef = useRef(liveGeneration);
  liveGenerationRef.current = liveGeneration;

  const activateFrame = useCallback(
    (frameId: string) => {
      setActiveFrameRuntime(frameId);
      void advanceLiveSlot(frameId);
      // active_frame 持久化（A20 刷新恢复；面板同一真相 A02）。superseded
      // 静默（G15 语义 —— 下轮 spec 回灌对齐）。
      void commitFrameProductPatch({ active_frame: frameId }).catch((e) => {
        devOnly.warn('[frame-grid] active_frame commit superseded/failed:', e);
      });
    },
    [advanceLiveSlot],
  );

  const handleClose = useCallback(() => {
    void advanceLiveSlot(null).then(() => {
      closeFrameGrid();
      clearFrameObservations(); // 评审 M2：关闭即失效（不回放旧声明）
    });
  }, [advanceLiveSlot]);

  // ── 相机草稿：sessionStorage 恢复（客户端本地，永不进 schema）。──
  useEffect(() => {
    if (!gridState.open || !sessionId) return;
    const drafts = new Map<string, CameraSnapshot>();
    for (const slot of product.slots) {
      try {
        const raw = sessionStorage.getItem(draftKey(sessionId, slot.frameId));
        if (raw) {
          const parsed = JSON.parse(raw) as CameraSnapshot;
          if (Array.isArray(parsed.center) && typeof parsed.zoom === 'number') {
            drafts.set(slot.frameId, parsed);
          }
        }
      } catch { /* 损坏草稿如实忽略 */ }
    }
    setCameraDrafts(drafts);
    const activeInvalid =
      !gridState.activeFrameId || !product.slots.some((s) => s.frameId === gridState.activeFrameId);
    if (activeInvalid) {
      const initial = product.activeFrame ?? product.slots[0]?.frameId ?? null;
      if (initial) setActiveFrameRuntime(initial);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在打开/会话变化时恢复
  }, [gridState.open, sessionId]);

  // ── 会话切换：快照/期望相机/草稿/hub/live 全失效（A14）。──
  useEffect(() => {
    const previous = sessionRef.current;
    sessionRef.current = sessionId ?? null;
    if (previous && previous !== sessionId) {
      snapshotStoreRef.current!.invalidateSession(previous);
      clearFrameObservations(); // 评审 M2：会话切换旧观察行不得进入新会话
      setExpectedCameras(new Map());
      setCameraDrafts(new Map());
      hubRef.current?.dispose();
      hubRef.current = null;
      setLiveFrameId(null);
      setWarmupQueue([]);
    }
  }, [sessionId]);

  // ── 实例预算观察（A09 披露 + 调度依据）。──
  useEffect(() => {
    if (!gridState.open) return;
    return mapInstanceRegistry.subscribe(() => setRegistryTick((t) => t + 1));
  }, [gridState.open]);
  useEffect(() => {
    return snapshotStoreRef.current!.subscribe(() => setSnapshotTick((t) => t + 1));
  }, []);

  // 预算判定（A09）：网格自身至多占 1 个 frame 槽位 —— 可挂载条件 =
  // 「其它实例（主图/对比等）+ 本网格 1 个槽位 ≤ 预算」。把自身槽位计入
  // 会造成 mount→满→unmount→空→mount 振荡（评审发现）。
  const gridFrameRegistered =
    !!liveFrameId && mapInstanceRegistry.has(`frame:${liveFrameId}`);
  const frameMountable =
    mapInstanceRegistry.liveCount() - (gridFrameRegistered ? 1 : 0) + 1 <=
    mapInstanceRegistry.budget();

  // ── 网格打开：live 槽位 = 活动画幅；warmup 队列 = 其余无快照帧（预算内）。──
  // registryTick（M4）：预算被占用（对比视图）早退后，释放时自动重排。
  // 打开时一次性排程（advanceLiveSlot/slots 经 ref 语义）。
  /* eslint-disable react-hooks/exhaustive-deps */
  useEffect(() => {
    if (!gridState.open || !bound) return;
    if (mapInstanceRegistry.liveCount() + 1 > mapInstanceRegistry.budget()) return; // 预算被占：不排程
    const active = gridState.activeFrameId ?? product.activeFrame ?? product.slots[0]?.frameId ?? null;
    void advanceLiveSlot(active);
    const needWarmup = product.slots
      .map((s) => s.frameId)
      .filter((id) => id !== active && !snapshotStoreRef.current!.peek(id));
    setWarmupQueue(needWarmup);
  }, [gridState.open, bound, registryTick]);
  /* eslint-enable react-hooks/exhaustive-deps */

  // ── warmup 驱动：live 帧落定（reconciled 或 typed error）后推进队列。──
  useEffect(() => {
    if (!gridState.open || !liveFrameId) return;
    if (mapInstanceRegistry.liveCount() > mapInstanceRegistry.budget()) {
      setWarmupQueue([]);
      return;
    }
    const fact = mountedFacts.get(liveFrameId);
    const settled =
      !!fact &&
      (!fact.errorCode
        ? fact.resolvedSpecRevision === liveGeneration
        : true); // 错误也是落定（失败帧不阻塞其他画幅 —— P4）
    if (!settled) return;
    if (warmupQueue.length === 0) {
      if (liveFrameId !== gridState.activeFrameId) {
        void advanceLiveSlot(gridState.activeFrameId);
      }
      return;
    }
    const [next, ...rest] = warmupQueue;
    setWarmupQueue(rest);
    void advanceLiveSlot(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 驱动器按事实推进
  }, [liveFrameId, mountedFacts, warmupQueue, liveGeneration, gridState.open]);

  // ── hub 生命周期：打开创建 / 关闭销毁。──
  useEffect(() => {
    if (!gridState.open || !bound) return;
    const onExpected = (frameId: string, camera: CameraSnapshot) => {
      setExpectedCameras((prev) => {
        const nextMap = new Map(prev);
        nextMap.set(frameId, camera);
        return nextMap;
      });
      // 期望相机漂移 → 该帧快照失效（P3：不强行创建 context）。
      const snap = snapshotStoreRef.current!.peek(frameId);
      if (snap?.camera) {
        const drifted =
          Math.abs(snap.camera.center[0] - camera.center[0]) > CAMERA_DRIFT_EPS ||
          Math.abs(snap.camera.center[1] - camera.center[1]) > CAMERA_DRIFT_EPS ||
          Math.abs(snap.camera.zoom - camera.zoom) > CAMERA_DRIFT_EPS;
        if (drifted) snapshotStoreRef.current!.invalidateFrame(frameId);
      }
    };
    const hub = new FrameCameraHub({
      syncPan: gridState.syncPan,
      syncZoom: gridState.syncZoom,
      linkMain: gridState.linkMain,
      onExpectedCamera: onExpected,
    });
    hubRef.current = hub;
    return () => {
      hub.dispose();
      hubRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 打开期间 hub 恒一
  }, [gridState.open, bound]);

  useEffect(() => {
    hubRef.current?.setSyncDimensions(gridState.syncPan, gridState.syncZoom);
    hubRef.current?.setLinkMain(gridState.linkMain);
  }, [gridState.syncPan, gridState.syncZoom, gridState.linkMain]);

  useEffect(() => {
    hubRef.current?.setActiveFrame(liveFrameId === gridState.activeFrameId ? liveFrameId : gridState.activeFrameId);
  }, [liveFrameId, gridState.activeFrameId]);

  // 主图 attach（mapLoaded 门 —— ComparisonView 同款）。
  useEffect(() => {
    if (!gridState.open || !mapLoaded) return;
    const hub = hubRef.current;
    const primary = primaryMapRef.current?.getMap();
    if (!hub || !primary) return;
    return hub.attach({
      id: '__main__',
      kind: 'main',
      map: primary as unknown as never,
      syncGroup: null,
      generation: 1,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 主图实例恒一
  }, [gridState.open, mapLoaded]);

  // 帧表身份串（键盘/后继 effect 的 deps —— 同数换 id 时刷新闭包，评审 m3）。
  const frameIdsKey = product.slots.map((x) => x.frameId).join('|');

  // ── Escape 关闭 + '[' / ']' 切换活动画幅（A25）。──
  useEffect(() => {
    if (!gridState.open || !bound) return;
    const frameIds = product.slots.map((s) => s.frameId);
    const activateNeighbor = (dir: 1 | -1) => {
      const cur = frameIds.indexOf(gridState.activeFrameId ?? '');
      const next = frameIds[(cur + dir + frameIds.length) % frameIds.length] ?? frameIds[0];
      if (next) activateFrame(next);
    };
    const unregisterEscape = registerEscapeHandler(10, (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        handleClose();
        return true;
      }
      return false;
    });
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return;
      if (event.key === '[') {
        event.preventDefault();
        activateNeighbor(-1);
      } else if (event.key === ']') {
        event.preventDefault();
        activateNeighbor(1);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      unregisterEscape();
      window.removeEventListener('keydown', onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 键盘按当前帧表（activateFrame/handleClose 稳定引用语义）
  }, [gridState.open, gridState.activeFrameId, bound, frameIdsKey]);

  // ── 帧删除/禁用：活动画幅确定性后继（A13 + 反馈）。──
  // 后继语义 = 被删者在**删除前产品序**中的下一个可用帧（环形）。因此
  // 保留上一代产品序（prevOrderRef），删除发生后用旧序定位被删者位置。
  const prevOrderRef = useRef<string[]>([]);
  useEffect(() => {
    if (!bound) {
      prevActiveRef.current = null;
      prevOrderRef.current = [];
      return;
    }
    const ids = product.slots.map((s) => s.frameId);
    const current = gridState.activeFrameId;
    if (current && !ids.includes(current)) {
      const layoutFrames = committedSpec?.layout?.frames ?? [];
      const enabledIds = new Set(
        (layoutFrames ?? [])
          .filter((f) => !f || f.enabled !== false)
          .map((f) => (f && typeof f.id === 'string' ? f.id : null))
          .filter((id): id is string => id !== null),
      );
      // 删除前序（含被删者）；首次快照缺失时退化为当前序。
      const orderWithRemoved = prevOrderRef.current.includes(current)
        ? prevOrderRef.current
        : [...ids, current];
      const successor = selectActiveSuccessor(orderWithRemoved, current, (id) => enabledIds.has(id) && ids.includes(id));
      prevActiveRef.current = successor;
      setActiveFrameRuntime(successor);
      void advanceLiveSlot(successor);
    } else {
      prevActiveRef.current = current;
    }
    prevOrderRef.current = ids;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 产品身份变化驱动
  }, [bound, product.slots.length, gridState.activeFrameId, committedSpec]);

  // ── 帧有效文档解析（composed live spec → 帧文档；typed error → 帧 error）。──
  const liveFrameSpec = useMemo(() => {
    if (!gridState.open || !liveFrameId) return null;
    const spec0 = composeLiveMapSpec(
      getCommittedMapSpec(),
      { layers, processLayers: {}, activeFilters: {}, selectionFilters: {}, is3D: false },
      undefined,
      undefined,
    );
    const hudOwnedRefs = new Set(
      layers.filter((l) => typeof l._refId === 'string').map((l) => l._refId as string),
    );
    const resolved = injectResolvedRefSources(
      spec0,
      sessionId
        ? { sessionId, ownerToken: ownerToken ?? sessionTokenRef?.current ?? null }
        : null,
      hudOwnedRefs,
    );
    try {
      // A08 接线（评审 M5）：挂载前显式投影判定 —— MapSpec 运行时全 3857，
      // 帧视图投影字段缺席视为兼容（旧数据兼容）；未来字段出现时在此拒绝。
      const layoutFrame = (getCommittedMapSpec()?.layout?.frames ?? []).find(
        (f) => f && f.id === liveFrameId,
      ) as { projection?: string } | undefined;
      const compat = checkProjectionCompat(layoutFrame?.projection ?? null, 'EPSG:3857');
      if (!compat.ok) {
        return {
          ok: false as const,
          error: new FrameEffectiveSpecError('frame_layer_unavailable', `A08 ${compat.reason}`),
        };
      }
      const out = resolveFrameEffectiveSpec(resolved, getCommittedMapSpec()?.frame_product, liveFrameId);
      return { ok: true as const, out };
    } catch (e) {
      return {
        ok: false as const,
        error:
          e instanceof FrameEffectiveSpecError
            ? e
            : new FrameEffectiveSpecError('frame_layer_unavailable', String(e)),
      };
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 真相世代驱动重算
  }, [gridState.open, liveFrameId, layers, liveGeneration, refSourcesGeneration, sessionId, ownerToken]);

  // ── 只读运行时投影（P0 契约的唯一消费点）。──
  const slots: FrameRuntimeSlotView[] = useMemo(() => {
    if (!gridState.open) return [];
    void registryTick;
    void snapshotTick;
    void refSourcesGeneration;
    const snapshotFacts = new Map<string, { frameId: string; fingerprint: string; specRevision: number; dataRevision: number | null; viewportSize: { width: number; height: number } | null }>();
    for (const slot of product.slots) {
      const e = snapshotStoreRef.current!.peek(slot.frameId);
      if (e) snapshotFacts.set(slot.frameId, e);
    }
    return projectFrameRuntime({
      spec: committedSpec,
      specRevision: liveGeneration,
      activeFrameId: liveFrameId === gridState.activeFrameId ? liveFrameId : gridState.activeFrameId,
      mounted: mountedFacts,
      snapshots: snapshotFacts,
      cameraDrafts,
      expectedCameras,
      viewportSize,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 观察面输入
  }, [gridState.open, committedSpec, liveGeneration, gridState.activeFrameId, mountedFacts, cameraDrafts, expectedCameras, viewportSize, registryTick, snapshotTick, product.slots.length]);

  // ── 逐帧观察发布（P6：页面挂点 rows）。──
  useEffect(() => {
    if (!gridState.open) return;
    publishFrameObservations({
      sessionId: sessionRef.current ?? '',
      specRevision: liveGeneration,
      slots,
      snapshotStats: snapshotStoreRef.current!.stats(),
      liveCount: mapInstanceRegistry.liveCount(),
      budget: mapInstanceRegistry.budget(),
    });
     
  }, [slots, liveGeneration, gridState.open]);

  // ── 事实回调（FrameViewport → mountedFacts）。──
  const handleMountedFact = useCallback(
    (fact: {
      frameId: string;
      generation: number;
      resolvedSpecRevision: number;
      resolvedDataRevision: number | null;
      errorCode: string | null;
      errorMessage?: string;
    }) => {
      setMountedFacts((prev) => {
        // 旧代际迟到事实不覆盖新代际（A13：不把旧事件写到新帧）。
        const cur = prev.get(fact.frameId);
        if (cur && cur.generation > fact.generation) return prev;
        const next = new Map(prev);
        next.set(fact.frameId, fact);
        return next;
      });
    },
    [],
  );

  // 解析失败（帧文档 typed error）也落定为事实 —— 失败帧不阻塞其他画幅
  // 的预热/激活（P4），错误在瓦片上可见（A19）。
  useEffect(() => {
    if (!gridState.open || !liveFrameId) return;
    if (liveFrameSpec && !liveFrameSpec.ok) {
      handleMountedFact({
        frameId: liveFrameId,
        generation: mountedFacts.get(liveFrameId)?.generation ?? 0,
        resolvedSpecRevision: -1,
        resolvedDataRevision: null,
        errorCode: liveFrameSpec.error.code,
        errorMessage: liveFrameSpec.error.message,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveFrameSpec, liveFrameId, gridState.open]);

  const handleCameraDraft = useCallback((frameId: string, camera: CameraSnapshot) => {
    setCameraDrafts((prev) => {
      const next = new Map(prev);
      next.set(frameId, camera);
      return next;
    });
    const sid = sessionRef.current;
    if (sid) {
      try {
        sessionStorage.setItem(draftKey(sid, frameId), JSON.stringify(camera));
      } catch { /* 配额满如实忽略 */ }
    }
  }, []);

  // ── live 帧 hub 接线（map 就绪后 attach；轮询容许 map 异步就绪）。──
  const liveAttachedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!gridState.open || !liveFrameId) {
      liveAttachedRef.current = null;
      return;
    }
    const hub = hubRef.current;
    if (!hub) return;
    const timer = window.setInterval(() => {
      const map = viewportHandleRef.current?.getMap();
      if (!map) return;
      // 评审 M3：身份校验 —— context loss 重建后 maplibre 实例更换，旧
      // 绑定必须重挂（否则同步组对该帧静默失效直到用户切换帧）。
      const attachedMap = liveAttachedRef.current === liveFrameId ? hub.entryMap(liveFrameId) : null;
      if (attachedMap === (map as unknown as never)) return;
      hub.attach({
        id: liveFrameId,
        kind: 'frame',
        map: map as unknown as never,
        syncGroup: product.slots.find((s) => s.frameId === liveFrameId)?.syncGroup ?? null,
        generation: mountedFacts.get(liveFrameId)?.generation ?? 1,
        // 评审 M5：锁定 extent（layout.frames[].extent）→ 拒绝相机写入
        //（A17 经 cameraWriteGuard 纯函数判定，不另写第二套真相）。
        lockedExtent: !cameraWriteGuard(
          (committedSpec?.layout?.frames ?? []).find((f) => f && f.id === liveFrameId) ?? null,
        ).ok,
      });
      hub.setActiveFrame(gridState.activeFrameId);
      const prevAttached = liveAttachedRef.current;
      if (prevAttached && prevAttached !== liveFrameId) hub.detach(prevAttached);
      liveAttachedRef.current = liveFrameId;
    }, 200);
    return () => {
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gridState.open, liveFrameId, gridState.activeFrameId, product.slots.length]);

  // 未挂载帧登记（期望相机记录对象）。
  useEffect(() => {
    if (!gridState.open) return;
    hubRef.current?.noteUnmounted(
      product.slots
        .filter((s) => s.frameId !== liveFrameId)
        .map((s) => ({ frameId: s.frameId, syncGroup: s.syncGroup })),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gridState.open, liveFrameId, product.slots.length]);

  if (!bound) return null;

  if (!gridState.open) {
    return (
      <div className="absolute bottom-4 right-3 z-30">
        <button
          type="button"
          onClick={() => openFrameGrid()}
          className="flex items-center gap-1.5 rounded-pill border border-edge-subtle bg-surface-overlay px-3 py-1.5 text-micro text-ink shadow-sm hover:bg-surface-hover"
          data-testid="frame-grid-open-chip"
          aria-label={t('frameGrid.open')}
        >
          <Camera className="h-3.5 w-3.5" />
          {t('frameGrid.chip', { count: product.slots.length })}
        </button>
      </div>
    );
  }

  const budgetBlocked = !frameMountable;
  const columns = Math.max(1, Math.min(4, product.columns || product.slots.length || 1));

  const liveViewportNode = (frameId: string): ReactNode => (
    <FrameViewport
      key={`live-viewport-${frameId}`}
      ref={(h: FrameViewportHandle | null) => {
        viewportHandleRef.current = h;
      }}
      frameId={frameId}
      effectiveSpec={liveFrameSpec && liveFrameSpec.ok ? liveFrameSpec.out.document : null}
      onMountedFact={handleMountedFact}
      onCameraDraft={handleCameraDraft}
      mapStyle={mapStyle}
      transformRequest={transformRequest}
      initialCamera={
        cameraDrafts.get(frameId) ??
        expectedCameras.get(frameId) ??
        readPrimaryCamera(primaryMapRef) ??
        null
      }
      desiredSpecRevision={liveGeneration}
      desiredDataRevision={
        product.slots.find((s) => s.frameId === frameId)?.binding?.contentRevision ?? null
      }
      lockedExtentOnly={!cameraWriteGuard(
        (committedSpec?.layout?.frames ?? []).find((f) => f && f.id === frameId) ?? null,
      ).ok}
    />
  );

  return (
    <div className="absolute inset-0 z-40 flex flex-col bg-surface" data-testid="frame-grid-root">
      {/* 工具条：产品标题 + scope 徽标 + 同步开关 + 预算 + 关闭 */}
      <div className="flex flex-wrap items-center gap-2 border-b border-edge-subtle bg-surface-overlay px-3 py-2" data-testid="frame-grid-toolbar">
        <span className="text-body font-medium text-ink">
          {product.gridTitle || t('frameGrid.defaultTitle')}
        </span>
        <span
          className="rounded-pill bg-status-accent-soft px-2 py-0.5 text-micro text-status-accent"
          data-testid="frame-grid-scope-chip"
        >
          {gridState.activeFrameId
            ? t('frameGrid.scopeFrameOnly', { frameId: gridState.activeFrameId })
            : t('frameGrid.scopeFrameOnlyEmpty')}
        </span>
        <span className="text-micro text-ink-muted" data-testid="frame-grid-budget">
          {t('frameGrid.liveBudget', {
            live: mapInstanceRegistry.liveCount(),
            budget: mapInstanceRegistry.budget(),
          })}
        </span>
        <label className="flex items-center gap-1 text-micro text-ink-muted">
          <input
            type="checkbox"
            checked={gridState.syncPan}
            onChange={(e) => setFrameGridSyncDimensions(e.target.checked, gridState.syncZoom)}
            aria-label={t('frameGrid.syncPan')}
          />
          {t('frameGrid.syncPanShort')}
        </label>
        <label className="flex items-center gap-1 text-micro text-ink-muted">
          <input
            type="checkbox"
            checked={gridState.syncZoom}
            onChange={(e) => setFrameGridSyncDimensions(gridState.syncPan, e.target.checked)}
            aria-label={t('frameGrid.syncZoom')}
          />
          {t('frameGrid.syncZoomShort')}
        </label>
        <button
          type="button"
          onClick={() => setFrameGridLinkMain(!gridState.linkMain)}
          className="flex items-center gap-1 rounded-pill border border-edge-subtle px-2 py-0.5 text-micro text-ink-muted hover:bg-surface-hover"
          aria-label={gridState.linkMain ? t('frameGrid.unlinkMain') : t('frameGrid.linkMainAction')}
          data-testid="frame-grid-link-main"
        >
          {gridState.linkMain ? <Link2 className="h-3 w-3" /> : <Link2Off className="h-3 w-3" />}
          {t('frameGrid.linkMain')}
        </button>
        <div className="flex-1" />
        <button
          type="button"
          onClick={handleClose}
          className="rounded p-1 text-ink-muted hover:bg-surface-hover hover:text-ink"
          aria-label={t('frameGrid.close')}
          data-testid="frame-grid-close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* 快照预热披露 */}
      {warmupQueue.length > 0 && (
        <div className="flex items-center gap-2 border-b border-edge-subtle bg-surface-sunken px-3 py-1 text-micro text-ink-muted" data-testid="frame-grid-warmup">
          <Loader2 className="h-3 w-3 animate-spin" />
          {t('frameGrid.warmup', { count: warmupQueue.length })}
        </div>
      )}

      {/* 预算被占用披露（A09） */}
      {budgetBlocked && !liveFrameId && (
        <div className="border-b border-edge-subtle bg-surface-sunken px-3 py-1 text-micro text-status-warning" data-testid="frame-grid-budget-blocked">
          {t('frameGrid.budgetBlocked')}
        </div>
      )}

      {/* 帧网格：live 视窗渲染进 live 占用者的瓦片 */}
      <div
        className="grid flex-1 auto-rows-fr gap-2 overflow-auto p-2"
        style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
        data-testid="frame-grid-tiles"
      >
        {slots.map((slot) => (
          <FrameTile
            key={slot.frameId}
            slot={slot}
            isLive={slot.frameId === liveFrameId}
            snapshot={snapshotStoreRef.current!.peek(slot.frameId)}
            viewportNode={
              slot.frameId === liveFrameId && !budgetBlocked && liveFrameSpec?.ok
                ? liveViewportNode(slot.frameId)
                : slot.frameId === liveFrameId && liveFrameSpec && !liveFrameSpec.ok
                  ? null
                  : null
            }
            resolveError={
              slot.frameId === liveFrameId && liveFrameSpec && !liveFrameSpec.ok
                ? liveFrameSpec.error
                : null
            }
            onActivate={() => activateFrame(slot.frameId)}
            onViewportSize={(w, h) => setViewportSize((prev) => (prev?.width === w && prev?.height === h ? prev : { width: w, height: h }))}
          />
        ))}
      </div>
    </div>
  );
}

/** 主图当前相机（活动画幅初值兜底 —— ComparisonView 对齐先例）。 */
function readPrimaryCamera(
  primaryMapRef: React.MutableRefObject<MapRef | null>,
): CameraSnapshot | null {
  const map = primaryMapRef.current?.getMap();
  if (!map) return null;
  const c = map.getCenter();
  return {
    center: [c.lng, c.lat],
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch(),
  };
}

interface FrameTileProps {
  slot: FrameRuntimeSlotView;
  isLive: boolean;
  snapshot: FrameSnapshotEntry | null;
  viewportNode: ReactNode;
  resolveError: FrameEffectiveSpecError | null;
  onActivate: () => void;
  onViewportSize: (width: number, height: number) => void;
}

/** 单帧瓦片：live 视窗 / 快照（版本标注 + 非 live 明示）/ 未观察占位。 */
function FrameTile({ slot, isLive, snapshot, viewportNode, resolveError, onActivate, onViewportSize }: FrameTileProps) {
  const tileRef = useRef<HTMLDivElement | null>(null);
  const t = useT('map');

  useEffect(() => {
    const el = tileRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect && rect.width > 0) onViewportSize(Math.round(rect.width), Math.round(rect.height));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [onViewportSize]);

  const stateLabel: Record<string, string> = {
    unmounted: t('frameGrid.stateUnmounted'),
    loading: t('frameGrid.stateLoading'),
    live: t('frameGrid.stateLive'),
    snapshot: t('frameGrid.stateSnapshot'),
    stale: t('frameGrid.stateStale'),
    error: t('frameGrid.stateError'),
  };
  return (
    <div
      ref={tileRef}
      className="relative overflow-hidden rounded-md border border-edge-subtle bg-surface-sunken"
      data-frame-id={slot.frameId}
      data-frame-state={slot.mountState}
      data-testid="frame-grid-tile"
    >
      {isLive && viewportNode ? (
        <div className="absolute inset-0">{viewportNode}</div>
      ) : snapshot && (slot.mountState === 'snapshot' || slot.mountState === 'stale') ? (
        // eslint-disable-next-line @next/next/no-img-element -- dataUrl 快照
        <img
          src={snapshot.dataUrl}
          alt={t('frameGrid.snapshotAlt', { title: slot.title })}
          className={`h-full w-full object-cover ${slot.mountState === 'stale' ? 'opacity-60' : ''}`}
        />
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-1 text-micro text-ink-muted">
          <span>{stateLabel[slot.mountState]}</span>
          <span className="text-caption">{t('frameGrid.activateHint')}</span>
        </div>
      )}
      {/* 底部信息条：标题 + 状态 + 版本标注（快照显式非 live —— A12） */}
      <div className="absolute inset-x-0 bottom-0 flex items-center gap-1.5 bg-black/50 px-2 py-1 text-white">
        <span className="truncate text-micro font-medium">{slot.title}</span>
        {isLive && slot.mountState === 'live' && (
          <span className="rounded-pill bg-emerald-500/80 px-1.5 text-caption" data-testid="frame-grid-live-badge">
            {t('frameGrid.liveBadge')}
          </span>
        )}
        <span className="rounded-pill bg-white/20 px-1.5 text-caption" data-testid="frame-grid-state-badge">
          {stateLabel[slot.mountState]}
        </span>
        <span className="text-caption opacity-75" data-testid="frame-grid-revision-badge">
          {t('frameGrid.revisionBadge', {
            spec: slot.resolvedSpecRevision ?? t('frameGrid.revisionNone'),
            data:
              slot.resolvedDataRevision !== null
                ? t('frameGrid.dataRevision', { rev: slot.resolvedDataRevision })
                : '',
          })}
        </span>
        {slot.syncGroup && (
          <span className="rounded-pill bg-white/20 px-1.5 text-caption">
            {t('frameGrid.groupBadge', { group: slot.syncGroup })}
          </span>
        )}
      </div>
      {/* 激活按钮（非 live 帧可点；键盘可达） */}
      {!isLive && (
        <button
          type="button"
          onClick={onActivate}
          className="absolute inset-0 h-full w-full cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--agent-accent)]"
          aria-label={t('frameGrid.activate', { frameId: slot.frameId })}
          data-testid="frame-grid-activate"
        />
      )}
      {isLive && slot.mountState === 'live' && (
        <div
          className="pointer-events-none absolute inset-0 rounded-md border-2"
          style={{ borderColor: 'var(--agent-accent)' }}
        />
      )}
      {(slot.error || (isLive && resolveError)) && (
        <div className="absolute inset-x-0 top-0 bg-red-600/85 px-2 py-0.5 text-caption text-white" data-testid="frame-grid-error">
          {(isLive && resolveError ? resolveError.code : slot.error?.code) ?? 'error'}
        </div>
      )}
    </div>
  );
}
