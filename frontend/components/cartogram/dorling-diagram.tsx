'use client';

/**
 * G47 真实图面：消费持久布局 artifact 的交互式 SVG（缩放/平移/选择/键盘）。
 *
 * 语义边界（写在 UI 上，不靠读者猜）：
 *  - 圆心是**示意排布**（diagram units），不是经纬度；
 *  - 只用 `preserveAspectRatio="xMidYMid meet"` + 统一 transform 缩放，
 *    任何长宽比下圆都不会变成椭圆（面积比例不变）；
 *  - 选择按 `feature_id` 上报，原地图联动由上层用同一身份完成。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useT } from '@/lib/i18n/useT';
import type { CartogramLayout } from '@/lib/cartogram/contracts';
import { hitTest, nextFeatureId, ringPath, toDiagramPoint } from '@/lib/cartogram/geometry';

const FILL = '#c2410c';
const STROKE = '#7c2d12';
const REFERENCE = '#94a3b8';

interface Props {
  layout: CartogramLayout;
  selectedId: string | null;
  onSelect: (featureId: string | null) => void;
  height?: number;
}

interface ViewState {
  scale: number;
  tx: number;
  ty: number;
}

const IDENTITY: ViewState = { scale: 1, tx: 0, ty: 0 };
const MIN_SCALE = 1;
const MAX_SCALE = 14;
/** 指针移动超过该距离（CSS px）即视为拖拽平移，不再当作点击。 */
const CLICK_DRAG_THRESHOLD = 4;

export function DorlingDiagram({ layout, selectedId, onSelect, height = 420 }: Props) {
  const t = useT('cartogram.diagram');
  const extent = Number(layout?.extent?.width) || 1000;
  const circles = useMemo(() => layout?.circles ?? [], [layout]);
  const ids = useMemo(() => circles.map((c) => c.feature_id), [circles]);
  const [view, setView] = useState<ViewState>(IDENTITY);
  const [hovered, setHovered] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const dragMoved = useRef(false);

  useEffect(() => {
    setView(IDENTITY);
  }, [layout?.candidate_digest]);

  const selected = useMemo(
    () => circles.find((c) => c.feature_id === selectedId) ?? null,
    [circles, selectedId],
  );
  const focused = useMemo(
    () => circles.find((c) => c.feature_id === hovered) ?? selected,
    [circles, hovered, selected],
  );

  const onWheel = useCallback((event: WheelEvent) => {
    event.preventDefault();
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    const fitScale = Math.min(rect.width / extent, rect.height / extent) || 1;
    const ox = (rect.width - extent * fitScale) / 2;
    const oy = (rect.height - extent * fitScale) / 2;
    const dx = (px - ox) / fitScale;
    const dy = (py - oy) / fitScale;
    setView((prev) => {
      const factor = event.deltaY < 0 ? 1.2 : 1 / 1.2;
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev.scale * factor));
      if (next === prev.scale) return prev;
      // 保持光标下的示意点不动（统一缩放，不做非等比拉伸）。
      const ratio = next / prev.scale;
      return {
        scale: next,
        tx: dx - (dx - prev.tx) * ratio,
        ty: dy - (dy - prev.ty) * ratio,
      };
    });
  }, [extent]);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    svg.addEventListener('wheel', onWheel, { passive: false });
    return () => svg.removeEventListener('wheel', onWheel);
  }, [circles.length, onWheel]);

  const onPointerDown = useCallback((event: React.PointerEvent<SVGSVGElement>) => {
    if (event.button !== 0) return;
    drag.current = { x: event.clientX, y: event.clientY, tx: view.tx, ty: view.ty };
    dragMoved.current = false;
    (event.target as Element).setPointerCapture?.(event.pointerId);
  }, [view.tx, view.ty]);

  const onPointerMove = useCallback((event: React.PointerEvent<SVGSVGElement>) => {
    const start = drag.current;
    if (!start) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const fitScale = Math.min(rect.width / extent, rect.height / extent) || 1;
    const dx = (event.clientX - start.x) / fitScale;
    const dy = (event.clientY - start.y) / fitScale;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > CLICK_DRAG_THRESHOLD) {
      dragMoved.current = true;
    }
    setView((prev) => ({ scale: prev.scale, tx: start.tx + dx, ty: start.ty + dy }));
  }, [extent]);

  const onPointerUp = useCallback(() => {
    drag.current = null;
  }, []);

  const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      event.preventDefault();
      onSelect(nextFeatureId(ids, selectedId, 1));
      return;
    }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      event.preventDefault();
      onSelect(nextFeatureId(ids, selectedId, -1));
      return;
    }
    if (event.key === 'Escape') {
      setView(IDENTITY);
      onSelect(null);
    }
  }, [ids, selectedId, onSelect]);

  const onClick = useCallback((event: React.MouseEvent<SVGSVGElement>) => {
    if (dragMoved.current) {
      // 拖动平移后的 click 不是选择：忽略本次并复位。
      dragMoved.current = false;
      return;
    }
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const point = toDiagramPoint(event.clientX - rect.left, event.clientY - rect.top, extent, {
      width: rect.width, height: rect.height,
    });
    const x = (point.x - view.tx) / view.scale;
    const y = (point.y - view.ty) / view.scale;
    onSelect(hitTest(circles, x, y));
  }, [circles, extent, onSelect, view.scale, view.tx, view.ty]);

  if (!circles.length) {
    return (
      <div
        data-g47-diagram="empty"
        className="rounded-md border border-dashed border-edge-subtle p-4 text-caption text-ink-muted"
      >
        {t('empty')}
      </div>
    );
  }

  const unit = layout?.legend?.unit || '';
  const zeroCount = (layout?.zero_ids ?? []).length;
  const missingCount = (layout?.missing_ids ?? []).length;
  const maxRadius = circles.reduce((max, c) => Math.max(max, c.radius), -Infinity);

  return (
    <div className="space-y-2" data-g47-diagram="root">
      <div
        role="application"
        aria-label={t('aria')}
        tabIndex={0}
        onKeyDown={onKeyDown}
        className="rounded-md border border-edge-subtle bg-surface focus:outline-none focus:ring-2 focus:ring-status-accent"
      >
        <svg
          ref={svgRef}
          viewBox={`0 0 ${extent} ${extent}`}
          preserveAspectRatio="xMidYMid meet"
          style={{ width: '100%', height, display: 'block', touchAction: 'none', cursor: drag.current ? 'grabbing' : 'grab' }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
          onClick={onClick}
          data-g47-view-scale={view.scale.toFixed(3)}
        >
          <rect x={0} y={0} width={extent} height={extent} fill="transparent" />
          <g transform={`translate(${view.tx} ${view.ty}) scale(${view.scale})`}>
            <g fill="none" stroke={REFERENCE} strokeWidth={1.2 / view.scale} opacity={0.7}>
              {(layout?.reference?.outlines ?? []).map((outline) => (
                <g key={outline.feature_id} data-reference-id={outline.feature_id}>
                  {outline.rings.map((ring, i) => {
                    const d = ringPath(ring);
                    return d ? <path key={`${outline.feature_id}-${i}`} d={d} /> : null;
                  })}
                </g>
              ))}
            </g>
            <g>
              {circles.map((c) => {
                const isSelected = c.feature_id === selectedId;
                const isHovered = c.feature_id === hovered;
                return (
                  <circle
                    key={c.feature_id}
                    data-feature-id={c.feature_id}
                    data-value={c.value}
                    data-radius={c.radius}
                    cx={c.x}
                    cy={c.y}
                    r={c.radius}
                    fill={FILL}
                    fillOpacity={isSelected ? 0.95 : 0.78}
                    stroke={isSelected ? '#111827' : STROKE}
                    strokeWidth={(isSelected ? 3 : isHovered ? 2 : 1.1) / view.scale}
                    onMouseEnter={() => setHovered(c.feature_id)}
                    onMouseLeave={() => setHovered(null)}
                    style={{ cursor: 'pointer' }}
                    aria-label={t('circle', {
                      label: c.label || c.feature_id, value: c.value, unit,
                    })}
                  >
                    <title>{`${c.label || c.feature_id}: ${c.value} ${unit}`}</title>
                  </circle>
                );
              })}
            </g>
            <g fontSize={11 / view.scale} fill="#0f172a" textAnchor="middle" pointerEvents="none">
              {circles
                .filter((c) => c.radius >= maxRadius * 0.42)
                .map((c) => (
                  <text key={`label-${c.feature_id}`} x={c.x} y={c.y + 4 / view.scale}>
                    {c.label || c.feature_id}
                  </text>
                ))}
            </g>
          </g>
        </svg>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-ink-muted">
        <span data-g47-status="1">
          {t('status', {
            status: layout.status === 'converged'
              ? t('converged') : t('notConverged'),
          })}
          {layout.publication_ready ? t('ready') : t('notReady')}
        </span>
        <span>{t('circles', { count: circles.length })}</span>
        <span>{t('zeros', { count: zeroCount })}</span>
        <span>{t('missing', { count: missingCount })}</span>
        <span>{t('hint')}</span>
      </div>

      {focused && (
        <div
          data-g47-selection={focused.feature_id}
          className="rounded-md border border-edge-subtle bg-surface px-2 py-1.5 text-caption text-ink"
        >
          <strong>{focused.label || focused.feature_id}</strong>
          {' · '}
          {focused.value} {unit}
          {' · '}
          <span className="text-ink-muted">
            {t('coords', {
              x: focused.x.toFixed(1), y: focused.y.toFixed(1),
            })}
          </span>
        </div>
      )}
    </div>
  );
}
