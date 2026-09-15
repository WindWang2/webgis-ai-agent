'use client';

/**
 * SpatialSketchTool — 画布 copilot 手势覆层（ADR-0194「画布即 Prompt」）。
 *
 * 挂在 <Map> 内（map-panel），覆盖层捕获指针手势：
 *   box_select      拖拽框选（pointerdown → move → up）
 *   freehand_lasso  自由手绘圈选（move 采样，up 收口）
 *   polygon_lasso   多边形套索（逐点点击，「完成」按钮或双击收口）
 *
 * 完成 → 虚线高亮（SVG polygon stroke-dasharray，copilotHighlight）+
 * SpatialAffordanceEnvelope 上报（fire-and-forget < 100ms 同步预算，
 * 见 lib/copilot/affordance）+ stage 随下一轮 turn 捎带。
 *
 * 捕获/投影的纯逻辑在 lib/copilot/sketch-capture —— 本组件只做粘合
 * （AGENTS 约定：逻辑可单测，组件薄）。与 sketch-editor（编辑用户草图
 * 图层、进 undo 栈）刻意分离：copilot 手势不写用户数据。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useT } from '@/lib/i18n/useT';
import { useHudStore } from '@/lib/store/useHudStore';
import type { CopilotToolId } from '@/lib/store/slices/copilotSlice';
import {
  buildCanvasAction,
  buildEnvelope,
  reportAffordance,
  ringToPolygon,
  type SpatialAffordanceEnvelope,
} from '@/lib/copilot/affordance';
import {
  boxRing,
  freehandRing,
  pointerToLocal,
  polygonLassoRing,
  ringBBox,
  ringPxToLngLat,
  type MapViewLike,
  type Pt,
} from '@/lib/copilot/sketch-capture';

/** 手势完成 → 信封出口（测试 seam；默认走通道上报 + stage + 遥测）。 */
export type AffordanceSink = (envelope: SpatialAffordanceEnvelope) => void;

export interface SpatialSketchToolProps {
  /** react-map-gl MapRef（getMap().project/unproject 精确投影）；可缺省。 */
  mapRef?: { current: { getMap?: () => MapLike | null } | null };
  /** 有真实 map 实例时注入（map.project/unproject 精度）；缺省线性近似。 */
  projectPoint?: (px: Pt) => [number, number];
  /** 线性投影参照系（无 mapRef/projectPoint 时必填）。 */
  mapView?: MapViewLike;
  /** 动作归属图层引用（用户当前聚焦图层等）。 */
  layerRefs?: string[];
  /** 测试/宿主可注入出口；缺省 = stage + 即时上报 + 延迟遥测。 */
  onAffordance?: AffordanceSink;
}

/** maplibre Map 的最小投影面（避免整包类型依赖进纯组件）。 */
interface MapLike {
  project: (lngLat: { lng: number; lat: number }) => { x: number; y: number };
  unproject: (point: [number, number]) => { lng: number; lat: number };
}

const TOOL_LABEL_KEYS: Record<CopilotToolId, string> = {
  box_select: 'boxSelect',
  freehand_lasso: 'freehandLasso',
  polygon_lasso: 'polygonLasso',
};

export function SpatialSketchTool({
  mapRef,
  projectPoint,
  mapView,
  layerRefs,
  onAffordance,
}: SpatialSketchToolProps) {
  const copilotTool = useHudStore((s) => s.copilotTool);
  const highlight = useHudStore((s) => s.copilotHighlight);
  const t = useT('copilot');
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [draft, setDraft] = useState<Pt[]>([]);
  const dragStartRef = useRef<Pt | null>(null);
  const pointersRef = useRef<Pt[]>([]);

  const project = useCallback(
    (px: Pt, rect: { width: number; height: number }): [number, number] => {
      if (projectPoint) return projectPoint(px);
      const map = mapRef?.current?.getMap?.() ?? null;
      if (map) {
        const ll = map.unproject([px[0], px[1]]);
        return [ll.lng, ll.lat];
      }
      if (mapView) return ringPxToLngLat([px], rect, mapView)[0];
      // 无参照系：诚实降级为屏幕坐标语义（bbox 仍可读，几何仅示警）。
      return [px[0], px[1]];
    },
    [mapRef, projectPoint, mapView],
  );

  const reset = useCallback(() => {
    dragStartRef.current = null;
    pointersRef.current = [];
    setDraft([]);
  }, []);

  const finishGesture = useCallback(
    (pxRing: Pt[] | null) => {
      const tool = useHudStore.getState().copilotTool;
      if (!tool || !pxRing || pxRing.length < 3) {
        reset();
        return;
      }
      const rect = rootRef.current?.getBoundingClientRect();
      const size = { width: rect?.width ?? 0, height: rect?.height ?? 0 };
      const lnglatRing = pxRing
        .slice(0, -1)
        .map((p) => project(p, size)) as [number, number][];
      const geometry = ringToPolygon(lnglatRing);
      const envelope = buildEnvelope([
        buildCanvasAction(tool, {
          ...(geometry ? { geometry, bbox: ringBBox(lnglatRing) } : {}),
          screen_px: {
            x: Math.round(Math.min(...pxRing.map((p) => p[0]))),
            y: Math.round(Math.min(...pxRing.map((p) => p[1]))),
            width: Math.round(Math.max(...pxRing.map((p) => p[0])) - Math.min(...pxRing.map((p) => p[0]))),
            height: Math.round(Math.max(...pxRing.map((p) => p[1])) - Math.min(...pxRing.map((p) => p[1]))),
          },
          ...(layerRefs?.length ? { layer_refs: layerRefs } : {}),
        }),
      ]);
      useHudStore.getState().setCopilotHighlight({ ring: pxRing, kind: tool, lnglatRing });
      if (onAffordance) {
        onAffordance(envelope);
      } else {
        const { latencyMs } = reportAffordance(envelope);
        const store = useHudStore.getState();
        store.stageCopilotEnvelope(envelope);
        store.recordCopilotReportLatency(latencyMs);
      }
      reset();
    },
    [project, layerRefs, onAffordance, reset],
  );

  // Escape 退出工具态（清除高亮 + 关工具）。
  useEffect(() => {
    if (!copilotTool) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        const store = useHudStore.getState();
        store.setCopilotHighlight(null);
        store.setCopilotTool(null);
        reset();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [copilotTool, reset]);

  if (!copilotTool) return null;

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (copilotTool === 'polygon_lasso') {
      // 套索：点击加点；不 capture 拖拽。
      const rect = rootRef.current?.getBoundingClientRect();
      if (!rect) return;
      e.stopPropagation();
      e.preventDefault();
      const pt = pointerToLocal(e.clientX, e.clientY, rect);
      pointersRef.current = [...pointersRef.current, pt];
      setDraft([...pointersRef.current]);
      return;
    }
    e.stopPropagation();
    e.preventDefault();
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect) return;
    const pt = pointerToLocal(e.clientX, e.clientY, rect);
    dragStartRef.current = pt;
    pointersRef.current = [pt];
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStartRef.current) return;
    e.stopPropagation();
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect) return;
    const pt = pointerToLocal(e.clientX, e.clientY, rect);
    if (copilotTool === 'box_select') {
      setDraft(boxRing(dragStartRef.current, pt));
    } else {
      pointersRef.current = [...pointersRef.current, pt];
      setDraft(freehandRing(pointersRef.current) ?? pointersRef.current);
    }
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStartRef.current) return;
    e.stopPropagation();
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect) return;
    const pt = pointerToLocal(e.clientX, e.clientY, rect);
    if (copilotTool === 'box_select') {
      finishGesture(boxRing(dragStartRef.current, pt));
    } else {
      finishGesture(freehandRing(pointersRef.current));
    }
  };

  const handleDoubleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (copilotTool !== 'polygon_lasso') return;
    e.stopPropagation();
    finishGesture(polygonLassoRing(pointersRef.current));
  };

  const width = rootRef.current?.getBoundingClientRect().width ?? 800;
  const height = rootRef.current?.getBoundingClientRect().height ?? 600;

  return (
    <div
      ref={rootRef}
      data-testid="copilot-sketch-overlay"
      data-copilot-tool={copilotTool}
      className="absolute inset-0 z-30 select-none"
      style={{ touchAction: 'none', cursor: 'crosshair' }}
      role="application"
      aria-label={t('overlayAria', { tool: t(TOOL_LABEL_KEYS[copilotTool]) })}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onDoubleClick={handleDoubleClick}
    >
      <svg width="100%" height="100%" className="pointer-events-none absolute inset-0">
        {draft.length >= 2 && (
          <polyline
            data-testid="copilot-draft"
            points={draft.map((p) => p.join(',')).join(' ')}
            fill="none"
            stroke="currentColor"
            className="text-sky-300"
            strokeWidth={1.5}
          />
        )}
        {highlight && highlight.ring.length >= 3 && (
          <polygon
            data-testid="copilot-highlight"
            points={highlight.ring.map((p) => p.join(',')).join(' ')}
            fill="currentColor"
            className="text-sky-400/20"
            stroke="currentColor"
            strokeDasharray="6 4"
            strokeWidth={2}
          />
        )}
        {copilotTool === 'polygon_lasso' && draft.length >= 3 && (
          <g>
            {draft.map((p, i) => (
              <circle key={i} cx={p[0]} cy={p[1]} r={3} className="fill-sky-300" />
            ))}
          </g>
        )}
      </svg>
      {copilotTool === 'polygon_lasso' && (
        <button
          type="button"
          data-testid="copilot-finish-lasso"
          onClick={(e) => {
            e.stopPropagation();
            finishGesture(polygonLassoRing(pointersRef.current));
          }}
          className="absolute left-1/2 top-3 -translate-x-1/2 rounded-md border border-edge bg-surface-raised px-3 py-1 text-xs text-ink shadow"
        >
          {t('finishLasso')}
        </button>
      )}
      {/* width/height 仅用于投影参照兜底的读取提示 */}
      <span hidden data-copilot-viewport={`${width}x${height}`} />
    </div>
  );
}
