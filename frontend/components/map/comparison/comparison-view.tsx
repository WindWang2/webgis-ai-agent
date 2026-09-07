'use client';

/**
 * ComparisonView — Wave 8 对比工作区覆盖层（before/after swipe + side-by-side）。
 *
 * 实现边界（评审约定）：
 * - 主地图本体不动：本组件只挂载**第二张** react-map-gl MaplibreMap 覆盖层，
 *   镜像主地图的底图样式（同 mapStyle / transformRequest），业务图层来自
 *   同一份 committed MapSpec（session-cursor.getCommittedMapSpec）经
 *   composeLiveMapSpec + MapSpecRuntime reconcile，**只保留副图层族**
 *   （`${family}__${sub}` 的 family 过滤，与 map-panel 的子层 id 约定一致）。
 * - swipe：覆盖层用 CSS clip-path inset 按 comparison.position 裁剪
 *   （clip-path 同时裁剪命中测试——裁剪区外的指针事件穿透到主地图）；
 *   分割把手 role=slider + 方向键 ±0.02（键盘可达）。
 * - side-by-side：同一张副图覆盖层固定裁剪在右半屏（分割缝不可拖动）。
 *   主地图保持全幅未动，因此每个窗格各显示相机中央的一侧 —— 相机全同步
 *   下两窗格严格对齐（主地图不可动的约束下的等价实现，与「两幅半宽视口」
 *   的观感差异已记录为已知取舍）。
 * - 相机同步：双向 move → resolveSyncPair（纯函数）→ jumpTo，isSyncing ref
 *   在写相机期间吞掉同步引发的事件（防反馈环），syncPan/syncZoom 由
 *   ComparisonState 决定补丁维度。
 *
 * 数据共享纪律（不复制载荷）：
 * - 源经**同一 ref id** 解析 —— 覆盖层复用 ref-source-resolver 的全局
 *   refId 缓存（LRU + in-flight 去重），HUD 已挂靠的 ref 走 live-spec 的
 *   ref_id 身份合并；ref GeoJSON 在内存中只有一份（双图共享同一 JS 对象）。
 * - MVT 矢量瓦片不走内存共享：每张 MapLibre 实例各自持有 HTTP 瓦片缓存
 *   （浏览器 HTTP cache 去重网络请求），除瓦片缓存外无额外载荷复制。
 *
 * z 纪律：容器 z-40 —— 盖过地图与其 chrome（z-20/z-30），压不过弹层（z-50）。
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import Map, { type MapRef } from 'react-map-gl/maplibre';
import { Columns2, X } from 'lucide-react';
import { useHudStore, type HudState } from '@/lib/store/useHudStore';
import type { StyleSpecification } from 'maplibre-gl';
import type { Layer } from '@/lib/types/layer';
import { MapSpecRuntime } from '@/lib/mapspec-runtime';
import { composeLiveMapSpec } from '@/lib/mapspec/live-spec';
import {
  injectResolvedRefSources,
  subscribeRefSources,
  getRefSourcesGeneration,
} from '@/lib/mapspec/ref-source-resolver';
import {
  getCommittedMapSpec,
  getMapSpecLiveGeneration,
  getPendingPresentation,
  getPendingRemoved,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import type { MapSpec, MapSpecSource } from '@/lib/mapspec-compiler/types';
import {
  applyCameraPatch,
  clampSwipePosition,
  comparisonFamilyId,
  readCamera,
  resolveSyncPair,
  SWIPE_KEYBOARD_STEP,
} from './comparison-sync';

/** 与 map-panel 的 DEFAULT_VIEW_STATE 同值（覆盖层挂载先落默认相机，onLoad 立即对齐主图）。 */
const DEFAULT_VIEW_STATE = {
  longitude: 116.4074,
  latitude: 39.9042,
  zoom: 4,
};

/** HUD layers 缺席时的稳定空表（引用恒定 —— 见选择器纪律注释）。 */
const EMPTY_LAYERS: Layer[] = [];

/** 族 id → HUD 行显示名（无行时如实回退族 id，控制条不撒谎）。 */
function layerNameOf(layers: Layer[], familyId: string | null): string | null {
  if (!familyId) return null;
  return layers.find((l) => comparisonFamilyId(l) === familyId)?.name ?? familyId;
}

interface ComparisonViewProps {
  /** 主地图的 react-map-gl ref（读相机 + 接收同步相机写入；主地图零改动）。 */
  primaryMapRef: React.MutableRefObject<MapRef | null>;
  /** 与主地图同源的底图样式（map-panel 的 currentMapStyle 原样透传）。 */
  mapStyle: string | StyleSpecification;
  /** 与主地图同源的瓦片请求凭证注入（会话令牌只发第一方 URL）。 */
  transformRequest?: (url: string, resourceType?: string) => {
    url: string;
    headers?: Record<string, string>;
  };
  sessionId?: string | null;
  ownerToken?: string | null;
  /** SEC-08：owner_token 经 SSE 迟到，读取必须走稳定 ref 的当前值。 */
  sessionTokenRef?: React.MutableRefObject<string | null>;
}

export function ComparisonView({
  primaryMapRef,
  mapStyle,
  transformRequest,
  sessionId,
  ownerToken,
  sessionTokenRef,
}: ComparisonViewProps) {
  // ── ComparisonState（原子选择器；比较状态缺席时按未激活收敛 —— HUD mock
  //    的最小状态形状不得炸渲染）。──
  const active = useHudStore((s: HudState) => s.comparison?.active ?? false);
  const kind = useHudStore((s: HudState) => s.comparison?.kind ?? 'swipe');
  const position = useHudStore((s: HudState) => s.comparison?.position ?? 0.5);
  const primaryLayerId = useHudStore((s: HudState) => s.comparison?.primaryLayerId ?? null);
  const secondaryLayerId = useHudStore((s: HudState) => s.comparison?.secondaryLayerId ?? null);
  const syncPan = useHudStore((s: HudState) => s.comparison?.syncPan ?? true);
  const syncZoom = useHudStore((s: HudState) => s.comparison?.syncZoom ?? true);
  const updateComparison = useHudStore((s: HudState) => s.updateComparison);
  const exitComparison = useHudStore((s: HudState) => s.exitComparison);
  // 选择器禁止内联分配（`?? []` 每次 getSnapshot 造新数组 → useSyncExternalStore
  // 判定快照不稳定 → 无限重渲染）。缺省走模块级冻结空数组。
  const layersRef = useHudStore((s: HudState) => s.layers);
  const layers = layersRef ?? EMPTY_LAYERS;
  // 主地图 onLoad 后才置 true —— 主图 MapLibre 实例的可用信号（同步监听挂载门）。
  const mapLoaded = useHudStore((s: HudState) => s.mapLoaded ?? false);

  const secondaryMapRef = useRef<MapRef | null>(null);
  const runtimeRef = useRef<MapSpecRuntime | null>(null);
  const isSyncingRef = useRef(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef(false);
  const [secondaryReady, setSecondaryReady] = useState(false);
  // 底图样式世代（basemap 切换 → setStyle 抹层 → 失效 runtime 缓存并重挂）。
  const [styleEpoch, setStyleEpoch] = useState(0);

  // 制图真相世代：committed spec 提交 / ref 数据解析完成 → 副图重 reconcile
  // （与 map-panel 的 reconcile effect 同款订阅对）。
  const liveGeneration = useSyncExternalStore(
    subscribeMapSpecLive,
    getMapSpecLiveGeneration,
    getMapSpecLiveGeneration,
  );
  const refSourcesGeneration = useSyncExternalStore(
    subscribeRefSources,
    getRefSourcesGeneration,
    getRefSourcesGeneration,
  );

  const primaryName = useMemo(() => layerNameOf(layers, primaryLayerId), [layers, primaryLayerId]);
  const secondaryName = useMemo(() => layerNameOf(layers, secondaryLayerId), [layers, secondaryLayerId]);

  // ── 副图业务图层挂载（只挂副图层族；diff/patch 全权交给 MapSpecRuntime）──
  useEffect(() => {
    if (!active || !secondaryReady || !secondaryLayerId) return;
    const secondary = secondaryMapRef.current?.getMap();
    if (!secondary) return;
    if (!runtimeRef.current) {
      runtimeRef.current = new MapSpecRuntime(secondary, {
        onStyleRecovery: () => setStyleEpoch((e) => e + 1),
      });
    }
    const spec0 = composeLiveMapSpec(
      getCommittedMapSpec(),
      {
        layers,
        processLayers: {},
        activeFilters: {},
        selectionFilters: {},
        is3D: false,
      },
      getPendingPresentation(),
      getPendingRemoved(),
    );
    // HUD 已挂靠的 ref 由 live-spec 合并；解析器只兜底无挂靠的（同一 refId
    // 全局缓存去重 —— 双图不重复拉取同一份数据）。
    const hudOwnedRefs = new Set(
      layers.filter((l) => typeof l._refId === 'string').map((l) => l._refId as string),
    );
    const resolved = injectResolvedRefSources(
      spec0,
      sessionId
        ? { sessionId, ownerToken: sessionTokenRef?.current ?? ownerToken ?? null }
        : null,
      hudOwnedRefs,
    );
    // 层族过滤（`${family}__${sub}`）+ 只保留被引用的源（ref-only 且无消费者
    // 的源不进入副图，避免多余的解析/拉取）。
    const familyLayers = (resolved.layers || []).filter(
      (l) => String(l.id || '').split('__')[0] === secondaryLayerId,
    );
    const usedSources = new Set(familyLayers.map((l) => String(l.source || '')));
    const sources: Record<string, MapSpecSource> = {};
    for (const [sid, source] of Object.entries(resolved.sources || {})) {
      if (usedSources.has(sid)) sources[sid] = source;
    }
    const filtered: MapSpec = { ...resolved, sources, layers: familyLayers };
    void runtimeRef.current
      .reconcileAsync(filtered)
      .catch((e) => console.warn('[comparison] secondary reconcile failed:', e));
  }, [active, secondaryReady, secondaryLayerId, layers, liveGeneration, refSourcesGeneration, styleEpoch, sessionId, ownerToken, sessionTokenRef]);

  // 底图样式身份变化 → 失效 runtime 样式缓存（对齐 map-panel 的 invalidateStyle 时机）。
  useEffect(() => {
    if (!active) return;
    runtimeRef.current?.invalidateStyle();
    setStyleEpoch((e) => e + 1);
  }, [mapStyle, active]);

  // 关闭 / 卸载：副图 runtime 随覆盖层销毁（副图 Map 卸载后 runtime 不得再持引用）。
  useEffect(() => {
    if (active) return;
    runtimeRef.current?.dispose();
    runtimeRef.current = null;
    setSecondaryReady(false);
  }, [active]);
  useEffect(() => {
    return () => {
      runtimeRef.current?.dispose();
      runtimeRef.current = null;
    };
  }, []);

  // ── 相机同步：副图 move → 主图（react-map-gl onMove 通道）。──
  const syncSecondaryToPrimary = useCallback(
    (evt: { viewState: { longitude: number; latitude: number; zoom: number; bearing?: number; pitch?: number } }) => {
      if (isSyncingRef.current) return; // 本次移动由同步写入引发 → 吞掉（防环）
      const primary = primaryMapRef.current?.getMap();
      const secondary = secondaryMapRef.current?.getMap();
      if (!primary || !secondary) return;
      const source = {
        center: [evt.viewState.longitude, evt.viewState.latitude] as [number, number],
        zoom: evt.viewState.zoom,
        bearing: evt.viewState.bearing ?? 0,
        pitch: evt.viewState.pitch ?? 0,
      };
      const patch = resolveSyncPair(source, readCamera(primary), syncPan, syncZoom);
      if (!patch) return;
      isSyncingRef.current = true;
      try {
        applyCameraPatch(primary, patch);
      } finally {
        isSyncingRef.current = false;
      }
      void secondary;
    },
    [primaryMapRef, syncPan, syncZoom],
  );

  // ── 相机同步：主图 move → 副图（主图实例事件通道；overlay 激活期间挂载）。──
  useEffect(() => {
    if (!active || !mapLoaded) return;
    const primary = primaryMapRef.current?.getMap();
    const secondary = secondaryMapRef.current?.getMap();
    if (!primary || !secondary) return;
    const onPrimaryMove = () => {
      if (isSyncingRef.current) return; // 同步写入引发的 move → 吞掉（防环）
      const patch = resolveSyncPair(readCamera(primary), readCamera(secondary), syncPan, syncZoom);
      if (!patch) return;
      isSyncingRef.current = true;
      try {
        applyCameraPatch(secondary, patch);
      } finally {
        isSyncingRef.current = false;
      }
    };
    primary.on('move', onPrimaryMove);
    return () => {
      primary.off('move', onPrimaryMove);
    };
  }, [active, mapLoaded, primaryMapRef, syncPan, syncZoom, secondaryReady]);

  // 副图就绪即对齐主图当前相机（进入对比 / 切层重挂不跳回默认视野）。
  const handleSecondaryLoad = useCallback(() => {
    setSecondaryReady(true);
    const primary = primaryMapRef.current?.getMap();
    const secondary = secondaryMapRef.current?.getMap();
    if (!primary || !secondary) return;
    isSyncingRef.current = true;
    try {
      applyCameraPatch(secondary, {
        center: [primary.getCenter().lng, primary.getCenter().lat],
        zoom: primary.getZoom(),
        bearing: primary.getBearing(),
        pitch: primary.getPitch(),
      });
    } finally {
      isSyncingRef.current = false;
    }
  }, [primaryMapRef]);

  // ── swipe 分割把手：指针拖拽 + 键盘（role=slider 契约）。──
  const setPosition = useCallback(
    (value: number) => updateComparison?.({ position: clampSwipePosition(value) }),
    [updateComparison],
  );

  const handleDividerPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    dragRef.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }, []);
  const handleDividerPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragRef.current) return;
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect || rect.width === 0) return;
      setPosition((e.clientX - rect.left) / rect.width);
    },
    [setPosition],
  );
  const endDrag = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    dragRef.current = false;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  }, []);

  const handleDividerKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        setPosition(position - SWIPE_KEYBOARD_STEP);
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        setPosition(position + SWIPE_KEYBOARD_STEP);
      } else if (e.key === 'Home') {
        e.preventDefault();
        setPosition(0);
      } else if (e.key === 'End') {
        e.preventDefault();
        setPosition(1);
      }
    },
    [position, setPosition],
  );

  if (!active) return null;

  // side-by-side = 固定对半的 swipe（无把手，静态分割缝）；swipe = position 驱动。
  const clipLeft = kind === 'swipe' ? position : 0.5;

  return (
    <div
      ref={containerRef}
      data-testid="comparison-overlay"
      role="region"
      // Review R1（architecture MAJOR-5）：主视图是未改造的主地图（全部
      // 可见图层在場），「A 与 B 对比」的措辞会误导 —— 如实声明右侧仅显示
      // 副图层族。
      aria-label={`图层对比视图：左为主视图（全部可见图层），右侧仅显示 ${secondaryName ?? '副视图'}（swipe 拖动分割线查看）`}
      className="pointer-events-none absolute inset-0 z-40"
    >
      {/* Review R1（GIS F5）：覆盖层 z-40 会盖住主图 attribution —— 副视图
          渲染的 basemap 瓦片必须在 pane 内自带署名（OSM/厂商红线）。
          副图自身的 attributionControl 仍是唯一署名源；这里只做主图被遮挡
          情况下的可发现性提示。 */}
      {/* 副图覆盖层：clip-path 裁剪渲染与命中测试（裁剪区外指针穿透到主地图）。
          容器 pointer-events-none + 地图容器 pointer-events-auto：只有可见区域
          接收手势。 */}
      <div
        data-testid="comparison-secondary-map"
        className="pointer-events-auto absolute inset-0"
        style={{ clipPath: `inset(0 0 0 ${clipLeft * 100}%)` }}
      >
        <Map
          ref={secondaryMapRef}
          id="comparison-secondary"
          initialViewState={DEFAULT_VIEW_STATE}
          onMove={syncSecondaryToPrimary}
          onLoad={handleSecondaryLoad}
          style={{ position: 'absolute', inset: 0 }}
          mapStyle={mapStyle}
          attributionControl={false}
          transformRequest={transformRequest}
        />
        {/* Review R1（GIS F5 MAJOR）：副视图瓦片署名 —— attributionControl
            关闭（避免 MapLibre 缺省控件与裁剪碰撞）不等于免署名；OSM/厂商
            条款要求可见 attribution。pane 内自带一行极简署名。 */}
        <div
          aria-hidden
          data-testid="comparison-attribution"
          className="pointer-events-none absolute bottom-0 right-0 z-[5] bg-black/40 px-1 py-0.5 text-[10px] leading-none text-white/85"
        >
          © OpenStreetMap contributors © CARTO
        </div>
      </div>

      {/* 分割线 / 分割缝 */}
      {kind === 'swipe' ? (
        <div
          data-testid="comparison-divider"
          role="slider"
          tabIndex={0}
          aria-label="对比分割线（左右拖动或方向键调整）"
          aria-orientation="horizontal"
          aria-valuemin={0}
          aria-valuemax={1}
          aria-valuenow={Math.round(position * 100) / 100}
          aria-valuetext={`分割位置 ${Math.round(position * 100)}%`}
          onPointerDown={handleDividerPointerDown}
          onPointerMove={handleDividerPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onKeyDown={handleDividerKeyDown}
          className="pointer-events-auto absolute inset-y-0 z-10 w-4 -translate-x-1/2 cursor-col-resize touch-none focus:outline-none"
          style={{ left: `${position * 100}%` }}
        >
          <div className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-white/90 shadow-agent-md" />
          <div className="absolute left-1/2 top-1/2 flex h-8 w-8 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-pill border border-edge-subtle bg-surface-raised text-ink shadow-agent-md">
            <Columns2 aria-hidden size={14} />
          </div>
        </div>
      ) : (
        <div
          aria-hidden
          className="absolute inset-y-0 z-10 w-0.5 bg-white/90 shadow-agent-md"
          style={{ left: '50%' }}
        />
      )}

      {/* 窗格标签 + 对比控制条 */}
      <div className="absolute left-1/2 top-14 z-20 -translate-x-1/2">
        <div className="pointer-events-auto flex items-center gap-1 rounded-pill border border-edge-subtle bg-surface-raised/95 px-1.5 py-1 shadow-agent-md">
          <span className="max-w-40 truncate px-1.5 text-micro font-medium text-ink" title={primaryName ?? undefined}>
            {primaryName ?? '主视图'}
          </span>
          <span aria-hidden className="text-micro text-ink-disabled">vs</span>
          <span className="max-w-40 truncate px-1.5 text-micro font-medium text-ink" title={secondaryName ?? undefined}>
            {secondaryName ?? '副视图'}
          </span>
          <span aria-hidden className="mx-0.5 h-4 w-px bg-edge-subtle" />
          <button
            type="button"
            data-testid="comparison-kind-swipe"
            aria-pressed={kind === 'swipe'}
            className={`rounded-pill px-2 py-0.5 text-micro ${
              kind === 'swipe'
                ? 'bg-status-accent-soft font-medium text-status-accent'
                : 'text-ink-secondary hover:bg-surface-hover hover:text-ink'
            }`}
            onClick={() => updateComparison?.({ kind: 'swipe' })}
          >
            滑动
          </button>
          {/* Review R1（GIS F2 CRITICAL）：side-by-side 诚实下线 —— 主图
              不动的约束下，双半屏相机使两图层永不覆盖同一地理（左=主图的
              左半、右=副图的右半），无法构成有效对比。词表保留（未来真
              双面板实现），UI 只暴露滑动模式。 */}
          <button
            type="button"
            data-testid="comparison-exit"
            aria-label="退出对比"
            title="退出对比"
            className="flex h-control-sm w-control-sm items-center justify-center rounded-pill text-ink-secondary hover:bg-surface-hover hover:text-ink"
            onClick={() => exitComparison?.()}
          >
            <X aria-hidden size={13} />
          </button>
        </div>
      </div>
    </div>
  );
}
