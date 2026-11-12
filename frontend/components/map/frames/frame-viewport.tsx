'use client';
/**
 * G25 FrameViewport —— 单画幅活地图视窗（P1/P3/P4）。
 *
 * 一个视窗 = 一张 react-map-gl MaplibreMap + 独立 MapSpecRuntime（Comparison
 * View 同门范本），渲染**帧有效文档**（grid 经 frame-effective-spec 解析：
 * layerOverrides + where/time_window 过滤 + 共享分级 —— 与服务端逐位一致）。
 *
 * 生命周期纪律（P4）：
 * - 挂载即登记 mapInstanceRegistry（'frame:{id}'，占预算）；卸载即 release；
 * - `webglcontextlost` → 代际 +1、整窗重建（key 变更由父级执行或本组件
 *   自身 state key 重建）、观察重绑（A16）；
 * - 卸载前的快照捕获走 `capture()`（waitForMapIdle → drawImage 离屏
 *   canvas → toDataURL；与 frame-composer/exporter 同技术）—— 由父级在
 *   切换 live 占用者前 await 调用，保证确定性捕获先于卸载；
 * - 相机事件由父级 hub 统一接管（本组件只暴露 map 通道 + moveend 草稿
 *   回调）；resize 由 react-map-gl 自适应（只影响本视窗，A15）。
 */
import { useCallback, useEffect, useRef, useState, useImperativeHandle, forwardRef } from 'react';
import Map, { type MapRef } from 'react-map-gl/maplibre';
import type { StyleSpecification } from 'maplibre-gl';
import { MapSpecRuntime } from '@/lib/mapspec-runtime';
import { waitForMapIdle } from '@/lib/export/highdpi';
import { mapInstanceRegistry } from './frame-instance-registry';
import { devOnly } from '@/lib/utils/logger';
import type { CameraSnapshot } from '@/lib/cartography/frame-sync';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

export interface FrameSnapshotCapture {
  dataUrl: string;
  bytes: number;
  camera: CameraSnapshot;
  width: number;
  height: number;
}

export interface FrameViewportHandle {
  /** 有界快照捕获（idle 后同步拷贝；失败返回 null —— 不伪造空画面）。 */
  capture: () => Promise<FrameSnapshotCapture | null>;
  /** 底层 map（hub 相机接线用；未就绪 null）。 */
  getMap: () => ReturnType<MapRef['getMap']> | null;
}

export interface FrameViewportProps {
  frameId: string;
  /** 帧有效文档（null = 解析中/失败——loading 或 error 由父级给出）。 */
  effectiveSpec: MapSpec | null;
  /** 挂载/渲染事实上报（resolved revision、错误码、代际）。 */
  onMountedFact: (fact: {
    frameId: string;
    generation: number;
    resolvedSpecRevision: number;
    resolvedDataRevision: number | null;
    errorCode: string | null;
    errorMessage?: string;
  }) => void;
  /** moveend 相机草稿（父级落 sessionStorage + 快照失效判据）。 */
  onCameraDraft: (frameId: string, camera: CameraSnapshot) => void;
  mapStyle: string | StyleSpecification;
  transformRequest?: (url: string, resourceType?: string) => {
    url: string;
    headers?: Record<string, string>;
  };
  initialCamera: CameraSnapshot | null;
  /** 本帧期望渲染的 spec/data revision（resolved 对照目标）。 */
  desiredSpecRevision: number;
  desiredDataRevision: number | null;
  onContextLost?: (frameId: string, generation: number) => void;
  /** 锁定 extent（A17，评审 r1）：true 时初始相机不做 draft/main 对齐放置
   * （初值 jumpTo 也过 extent 门 —— 锁定帧不被任何相机写入移动）。 */
  lockedExtentOnly?: boolean;
}

/**
 * 注：forwardRef 而非 ref prop —— react 19 之前兼容（与仓库其他组件一致）。
 */
export const FrameViewport = forwardRef<FrameViewportHandle, FrameViewportProps>(
  function FrameViewport(
    {
      frameId,
      effectiveSpec,
      onMountedFact,
      onCameraDraft,
      mapStyle,
      transformRequest,
      initialCamera,
      desiredSpecRevision,
      desiredDataRevision,
      onContextLost,
      lockedExtentOnly = false,
    },
    ref,
  ) {
    const mapRef = useRef<MapRef | null>(null);
    const runtimeRef = useRef<MapSpecRuntime | null>(null);
    const registrationRef = useRef<{ release: () => void; generation: number } | null>(null);
    // 底图世代（basemap 切换 → setStyle → runtime 缓存失效重挂）。
    const [styleEpoch, setStyleEpoch] = useState(0);
    // context loss 代际（重建 key —— 旧代际监听/快照全部作废）。
    const [rebuildEpoch, setRebuildEpoch] = useState(0);
    const [mapReady, setMapReady] = useState(false);
    // 最近一次成功 reconcile 的 revision 对照。
    const lastReconciledRef = useRef<{ spec: number; data: number | null } | null>(null);

    // ── 实例登记（挂载占预算 / 卸载释放；代际 = registry generation）。──
    useEffect(() => {
      const handle = mapInstanceRegistry.register('frame', `frame:${frameId}`);
      registrationRef.current = handle;
      return () => {
        handle.release();
        registrationRef.current = null;
      };
    }, [frameId]);

    // ── 卸载清理：runtime dispose（Map 实例随组件销毁，不残留引用）。──
    useEffect(() => {
      return () => {
        runtimeRef.current?.dispose();
        runtimeRef.current = null;
      };
    }, []);

    const reportFact = useCallback(
      (errorCode: string | null, errorMessage?: string) => {
        onMountedFact({
          frameId,
          generation: registrationRef.current?.generation ?? 0,
          resolvedSpecRevision: lastReconciledRef.current?.spec ?? -1,
          resolvedDataRevision: lastReconciledRef.current?.data ?? null,
          errorCode,
          errorMessage,
        });
      },
      [frameId, onMountedFact],
    );

    // ── 业务图层挂载（帧有效文档 → MapSpecRuntime；diff 全权交给 runtime）。──
    useEffect(() => {
      if (!mapReady) return;
      const map = mapRef.current?.getMap();
      if (!map) return;
      if (!runtimeRef.current) {
        runtimeRef.current = new MapSpecRuntime(map, {
          onStyleRecovery: () => setStyleEpoch((e) => e + 1),
        });
      }
      if (!effectiveSpec) return;
      runtimeRef.current
        .reconcileAsync(effectiveSpec)
        .then(() => {
          lastReconciledRef.current = { spec: desiredSpecRevision, data: desiredDataRevision };
          reportFact(null);
        })
        .catch((e: unknown) => {
          // 渲染失败 → 帧 error 态（局部可重试；不影响其他画幅）。
          reportFact('reconcile_error', e instanceof Error ? e.message : String(e));
          devOnly.warn('[frame-viewport] reconcile failed:', frameId, e);
        });
    }, [mapReady, effectiveSpec, desiredSpecRevision, desiredDataRevision, frameId, styleEpoch, rebuildEpoch, reportFact]);

    // ── 底图样式身份变化 → 失效 runtime 样式缓存（map-panel 同款时机）。──
    useEffect(() => {
      if (!mapReady) return;
      runtimeRef.current?.invalidateStyle();
      setStyleEpoch((e) => e + 1);
    }, [mapStyle, mapReady]);

  // ── context loss：canvas 事件 → 代际重建（A16）。──
  useEffect(() => {
    if (!mapReady) return;
    const canvas = mapRef.current?.getMap()?.getCanvas() as
      | (HTMLCanvasElement & { addEventListener?: unknown })
      | null;
    if (!canvas || typeof canvas.addEventListener !== 'function') return; // mock/jsdom 容错
      const onLost = (event: Event) => {
        event.preventDefault();
        const generation = registrationRef.current?.generation ?? 0;
        reportFact('webgl_context_lost', 'WebGL context lost — rebuilding viewport');
        onContextLost?.(frameId, generation);
        setRebuildEpoch((e) => e + 1); // key 重建（Map 卸载重挂）
      };
      canvas.addEventListener('webglcontextlost', onLost);
      return () => canvas.removeEventListener('webglcontextlost', onLost);
    }, [mapReady, frameId, rebuildEpoch, onContextLost, reportFact]);

    // ── 对外句柄：capture（idle → 离屏 drawImage → dataUrl）+ getMap。──
    useImperativeHandle(
      ref,
      () => ({
        getMap: () => mapRef.current?.getMap() ?? null,
        capture: async () => {
          const map = mapRef.current?.getMap();
          if (!map) return null;
          try {
            // 交互切换路径的捕获上限 2.5s：快速连点/Escape 不得串行长等待
            // （捕获失败如实 null —— 快照缺席优于交互卡顿）。
            await waitForMapIdle(map, 2_500);
            const live = map.getCanvas();
            const center = map.getCenter();
            // dataUrl 是不可变字符串 —— 直接从 live canvas 同步读取（idle 后
            // 像素即定格），不持 canvas/上下文引用（无泄漏面）。
            const dataUrl = live.toDataURL('image/png');
            // 尺寸口径 = CSS 像素（评审 m1）：瓦片 ResizeObserver 同口径，
            // 否则 HiDPI（DPR>1）下快照指纹的尺寸子句恒不匹配 → 全部误判
            // stale。canvas.width 是 device px，除以 pixelRatio 归一。
            const ratio = typeof map.getPixelRatio === 'function' ? map.getPixelRatio() : 1;
            return {
              dataUrl,
              bytes: dataUrl.length,
              camera: {
                center: [center.lng, center.lat],
                zoom: map.getZoom(),
                bearing: map.getBearing(),
                pitch: map.getPitch(),
              },
              width: Math.round(live.width / (ratio || 1)),
              height: Math.round(live.height / (ratio || 1)),
            };
          } catch {
            return null; // 捕获失败如实返回（不伪造空白成功 —— P6 契约）
          }
        },
      }),
      [],
    );

    const handleLoad = useCallback(() => {
      setMapReady(true);
      reportFact(null);
      // 初相机对齐（draft→expected→main 由父级排好；无则 spec view 落地）。
      // 锁定 extent 帧（评审 r1）：初值放置同样过 extent 门 —— 保持 spec view。
      if (initialCamera && !lockedExtentOnly) {
        mapRef.current?.getMap()?.jumpTo({
          center: initialCamera.center,
          zoom: initialCamera.zoom,
          bearing: initialCamera.bearing,
          pitch: initialCamera.pitch,
        });
      }
    }, [initialCamera, lockedExtentOnly, reportFact]);

    const handleMoveEnd = useCallback(() => {
      const map = mapRef.current?.getMap();
      if (!map) return;
      const center = map.getCenter();
      onCameraDraft(frameId, {
        center: [center.lng, center.lat],
        zoom: map.getZoom(),
        bearing: map.getBearing(),
        pitch: map.getPitch(),
      });
    }, [frameId, onCameraDraft]);

    const initialViewState = initialCamera
      ? {
          longitude: initialCamera.center[0],
          latitude: initialCamera.center[1],
          zoom: initialCamera.zoom,
          bearing: initialCamera.bearing,
          pitch: initialCamera.pitch,
        }
      : undefined;

    return (
      <Map
        key={`frame-map-${frameId}-g${registrationRef.current?.generation ?? 0}-r${rebuildEpoch}`}
        id={`frame-${frameId}`}
        ref={mapRef}
        mapStyle={mapStyle}
        transformRequest={transformRequest}
        initialViewState={initialViewState}
        onLoad={handleLoad}
        onMoveEnd={handleMoveEnd}
        style={{ width: '100%', height: '100%' }}
        attributionControl={false}
      />
    );
  },
);
