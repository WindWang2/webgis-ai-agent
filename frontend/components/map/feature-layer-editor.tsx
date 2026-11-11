'use client';
import { registerEscapeHandler } from '@/lib/utils/escape-stack';

/**
 * FeatureLayerEditor — 真实图层编辑手势面（G14 / WP08）。
 *
 * 边界：
 * - 编辑目标（editTarget.layerId/refId）来自 toolSlice；本组件只在目标
 *   在场时工作；手势工具与草图工具互斥（toolSlice 单字段语义）；
 * - 手势期间的几何只进本地 draft 源（wb-feedit-*），零 REST；手势结束
 *   经 layer-edit-bridge 走 preview→approve 服务端事务（一次手势一次
 *   revision）；Escape 取消不落任何服务端写入（V04/V05）；
 * - 图层渲染真相仍在 MapSpec/HUD 行 —— 提交成功后由回执驱动的
 *   refreshDatasetLayer 收敛，本组件不自行向权威层写数据；
 * - 状态面板（底部）用具体对象和动作描述；技术细节（ref/CAS）只在
 *   诊断 detail 里出现。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { MapRef } from 'react-map-gl/maplibre';
import type { GeoJSONSource, MapGeoJSONFeature, MapMouseEvent } from 'maplibre-gl';
import { useHudStore } from '@/lib/store/useHudStore';
import { getSessionIdentity } from '@/lib/store/session-identity';
import { getSelection, subscribeSelection, getSelectionGeneration } from '@/lib/selection/selection-store';
import { submitEditOps, featureIdFromProperties, type SubmitEditResult } from '@/lib/edit/layer-edit-bridge';
import { pickAuthoritativeFeature } from '@/lib/edit/mvt-pick';
import { preciseFeatureHit } from '@/lib/edit/geometry-hit';
import { useT } from '@/lib/i18n/useT';

const DRAFT_SOURCE = 'wb-feedit-src';
const DRAFT_LINE_LAYER = 'wb-feedit-draft-line';
const DRAFT_POINT_LAYER = 'wb-feedit-draft-point';
const EDIT_COLOR = '#f59e0b';

type Draft =
  | { kind: 'line' | 'polygon' | 'split'; points: [number, number][] }
  | null;

type EditStatus =
  | { state: 'idle' }
  | { state: 'submitting'; what: string }
  | { state: 'failed'; message: string; retry?: () => void }
  | { state: 'done'; what: string };

/**
 * 编辑目标要素获取（G24/P2 重构）：
 * - inline 图层：直接读 HUD 行 source.features（本地精确命中）；
 * - MVT 大图层：本地行没有内联要素 —— 候选 id → 服务端权威回查
 *   （pickEditTarget，见下）；绝不为拾取整包拉取 FeatureCollection。
 */

function closeRing(points: [number, number][]): [number, number][] {
  if (points.length >= 3) {
    const [x0, y0] = points[0];
    const [xn, yn] = points[points.length - 1];
    if (x0 !== xn || y0 !== yn) return [...points, [x0, y0]];
  }
  return points;
}

function ringOf(geometry: GeoJSON.Geometry): [number, number][] {
  if (geometry.type === 'LineString') return geometry.coordinates.map((c) => [c[0], c[1]]);
  if (geometry.type === 'Polygon') return geometry.coordinates[0].map((c) => [c[0], c[1]]);
  if (geometry.type === 'Point') return [[geometry.coordinates[0], geometry.coordinates[1]]];
  return [];
}

/**
 * 要素命中（G24/P2 起统一走 geometry-hit.preciseFeatureHit）：
 * 线=逐段距离（长线段中段可命中，A09）；面=外环减洞（A08）；
 * Multi* 按真实部件（A10）。返回命中距离供多候选择近。
 */
function featureHit(
  feature: GeoJSON.Feature,
  lngLat: [number, number],
  project: (p: [number, number]) => { x: number; y: number },
  point: { x: number; y: number },
  tolerancePx: number,
): { hit: boolean; distPx: number } {
  return preciseFeatureHit(feature, lngLat, project, point, tolerancePx);
}

export function FeatureLayerEditor({ mapRef }: { mapRef: React.RefObject<MapRef | null> }) {
  const t = useT();
  const mapReady = useHudStore((s) => s.mapLoaded);
  const tool = useHudStore((s) => s.activeMapTool);
  const editTarget = useHudStore((s) => s.editTarget);
  const setEditTarget = useHudStore((s) => s.setEditTarget);
  const setActiveMapTool = useHudStore((s) => s.setActiveMapTool);
  const layers = (useHudStore((s) => s.layers) ?? []) as Array<{ id: string; name: string; source?: unknown; _refId?: string; _descriptor?: unknown; _mapspecLayerId?: string; _tileUrl?: string }>;
  const [status, setStatus] = useState<EditStatus>({ state: 'idle' });
  const [attributesJson, setAttributesJson] = useState('');
  /** vertex 编辑：被选要素（HUD 行数据快照）与其几何 draft。 */
  const [vertexTarget, setVertexTarget] = useState<{ featureId: string; geometry: GeoJSON.Geometry } | null>(null);
  const [splitTarget, setSplitTarget] = useState<string | null>(null);
  const draftRef = useRef<Draft>(null);
  const [, forceRender] = useState(0);
  const bump = useCallback(() => forceRender((n) => n + 1), []);
  /** selection 订阅（merge 按钮可用性）。 */
  const [selectionGen, setSelectionGen] = useState(getSelectionGeneration());
  useEffect(() => subscribeSelection(() => setSelectionGen(getSelectionGeneration())), []);
  const selection = useMemo(() => getSelection(), [selectionGen]); // eslint-disable-line react-hooks/exhaustive-deps

  /* R3 P2-6：工具切换清理过期手势目标（split 目标随 split 工具退出而清）。 */
  useEffect(() => {
    if (tool !== 'split_feature' && splitTarget) setSplitTarget(null);
    if (tool !== 'edit_vertices' && vertexTarget) setVertexTarget(null);
  }, [tool, splitTarget, vertexTarget]);

  const targetLayer = layers.find((l) => l.id === editTarget?.layerId);
  const refId = editTarget?.refId ?? null;
  const sessionId = getSessionIdentity().sessionId;
  /* R3 P2-4：会话内编辑取数的中止纪律 —— 目标切换/卸载时中止在途刷新。 */
  const refreshAbortRef = useRef<AbortController | null>(null);
  useEffect(() => {
    refreshAbortRef.current = new AbortController();
    return () => { refreshAbortRef.current?.abort(); };
  }, [editTarget?.refId]);

  const getMap = useCallback(() => mapRef.current?.getMap() ?? null, [mapRef]);

  /**
   * 编辑目标拾取（G24/P2）：inline 图层沿用本地精确命中（A01 不回退）；
   * MVT/无内联数据图层走 queryRenderedFeatures 候选 id → 服务端权威回查
   * → 对原始几何精确命中（瓦片裁剪/简化几何绝不直接当编辑对象）。
   * 命中距离最近者胜（多候选可解释）；无稳定 id → noStableId 反馈。
   */
  const pickEditTarget = useCallback(async (
    lngLat: [number, number],
    point: { x: number; y: number },
  ): Promise<{ id: string; feature: GeoJSON.Feature } | null> => {
    const layer = layers.find((l) => l.id === editTarget?.layerId);
    if (!layer || !refId) return null;
    const m = getMap();
    if (!m) return null;
    const tolerance = 6;
    const inline = (layer.source as { features?: GeoJSON.Feature[] } | undefined)?.features;
    if (inline?.length) {
      const proj = (c: [number, number]) => m.project(c);
      let best: { feature: GeoJSON.Feature; dist: number } | null = null;
      for (const f of inline) {
        if (!f.geometry || f.geometry.type === 'GeometryCollection') continue;
        const r = featureHit(f, lngLat, proj, point, tolerance);
        if (!r.hit) continue;
        if (!best || r.distPx < best.dist) best = { feature: f, dist: r.distPx };
      }
      if (!best) return null;
      const fid = featureIdFromProperties(
        best.feature.properties as Record<string, unknown> | null,
        (best.feature as { id?: unknown }).id,
      );
      if (!fid) { setStatus({ state: 'failed', message: t('map.edit.noStableId') }); return null; }
      return { id: fid, feature: best.feature };
    }
    // MVT / ref 层：权威拾取（瓦片几何只是候选 id 载体）
    const descriptor = layer._descriptor as { content_revision?: number } | undefined;
    const picked = await pickAuthoritativeFeature({
      map: m,
      point,
      lngLat,
      layerId: layer.id,
      refId,
      revision: descriptor?.content_revision ?? null,
      tolerancePx: tolerance,
      signal: refreshAbortRef.current?.signal,
    });
    return picked ? { id: picked.id, feature: picked.feature } : null;
  }, [layers, editTarget, refId, getMap, t]);

  /* ─── draft 源挂载 + 数据同步（本地预览唯一渲染面） ─── */
  useEffect(() => {
    const map = getMap();
    if (!map || !mapReady || !editTarget) return;
    if (!map.getSource(DRAFT_SOURCE)) {
      map.addSource(DRAFT_SOURCE, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    }
    if (!map.getLayer?.(DRAFT_LINE_LAYER)) {
      map.addLayer({
        id: DRAFT_LINE_LAYER,
        type: 'line',
        source: DRAFT_SOURCE,
        filter: ['==', ['get', 'meta'], 'draft'],
        paint: { 'line-color': EDIT_COLOR, 'line-width': 2, 'line-dasharray': [2, 2] },
      });
    }
    if (!map.getLayer?.(DRAFT_POINT_LAYER)) {
      map.addLayer({
        id: DRAFT_POINT_LAYER,
        type: 'circle',
        source: DRAFT_SOURCE,
        paint: { 'circle-radius': 5, 'circle-color': EDIT_COLOR, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.5 },
      });
    }
    return () => {
      // 退出编辑清自管层（不动权威层）
      for (const id of [DRAFT_LINE_LAYER, DRAFT_POINT_LAYER]) {
        if (map.getLayer?.(id)) map.removeLayer(id);
      }
      if (map.getSource(DRAFT_SOURCE)) map.removeSource(DRAFT_SOURCE);
    };
  }, [mapReady, getMap, editTarget]);

  /* draft → source 数据同步 */
  useEffect(() => {
    const map = getMap();
    if (!map || !editTarget) return;
    const source = map.getSource(DRAFT_SOURCE) as GeoJSONSource | undefined;
    if (!source) return;
    const features: GeoJSON.Feature[] = [];
    const draft = draftRef.current;
    if (draft && draft.points.length) {
      if (draft.points.length >= 2) {
        features.push({
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: draft.kind === 'polygon' && draft.points.length >= 3
              ? [closeRing(draft.points).map((p) => p)]
              : draft.points,
          } as never,
          properties: { meta: 'draft' },
        } as never);
      }
      for (const p of draft.points) {
        features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: p }, properties: { meta: 'draft' } });
      }
    }
    if (vertexTarget) {
      for (const c of ringOf(vertexTarget.geometry)) {
        features.push({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: c },
          properties: { meta: 'draft' },
        });
      }
    }
    source.setData({ type: 'FeatureCollection', features } as never);
  }, [mapReady, getMap, editTarget, vertexTarget, status, selectionGen]);

  const parseAttributes = useCallback((): Record<string, unknown> | string => {
    const raw = attributesJson.trim();
    if (!raw) return {};
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
      return t('map.edit.attrsNotObject');
    } catch {
      return t('map.edit.attrsInvalidJson');
    }
  }, [attributesJson, t]);

  const submit = useCallback(async (ops: Parameters<typeof submitEditOps>[2], opts: { featureIds?: string[]; labelKey: string; what: string }) => {
    if (!sessionId || !refId) return;
    setStatus({ state: 'submitting', what: opts.what });
    const result: SubmitEditResult = await submitEditOps(sessionId, refId, ops, {
      featureIds: opts.featureIds,
      labelKey: opts.labelKey,
      signal: refreshAbortRef.current?.signal,
    });
    if (result.ok) {
      setStatus({ state: 'done', what: opts.what });
      setVertexTarget(null);
      setSplitTarget(null);
    } else if (result.stage === 'preview' && result.code === 'validation') {
      setStatus({ state: 'failed', message: result.findings?.[0]?.message ?? result.message ?? 'validation' });
    } else {
      const key = result.code === 'stale_revision' ? 'map.edit.staleConflict'
        : result.code === 'expired' ? 'map.edit.expired'
        : 'map.edit.networkError';
      setStatus({ state: 'failed', message: t(key) });
    }
  }, [sessionId, refId, t]);

  /* ─── 完成草稿（Enter / 双击）：一次手势 → 一次服务端事务 ─── */
  const completeDraft = useCallback(async () => {
    const draft = draftRef.current;
    if (!draft || !refId) return;
    draftRef.current = null;
    bump();
    if (draft.kind === 'split') {
      if (!splitTarget || draft.points.length < 2) { setSplitTarget(null); return; }
      await submit(
        [{ op: 'split_feature', wfid: splitTarget, line: { type: 'LineString', coordinates: draft.points } }],
        { labelKey: 'map.edit.splitFeature', what: t('map.edit.splitFeature') },
      );
      return;
    }
    if (draft.kind === 'line' && draft.points.length >= 2) {
      const attrs = parseAttributes();
      if (typeof attrs === 'string') { setStatus({ state: 'failed', message: attrs }); return; }
      await submit(
        [{ op: 'create_features', features: [{ geometry: { type: 'LineString', coordinates: draft.points }, ...(Object.keys(attrs).length ? { properties: attrs } : {}) }] }],
        { labelKey: 'map.edit.createLine', what: t('map.edit.createLine') },
      );
      return;
    }
    if (draft.kind === 'polygon' && draft.points.length >= 3) {
      const attrs = parseAttributes();
      if (typeof attrs === 'string') { setStatus({ state: 'failed', message: attrs }); return; }
      await submit(
        [{ op: 'create_features', features: [{ geometry: { type: 'Polygon', coordinates: [closeRing(draft.points)] }, ...(Object.keys(attrs).length ? { properties: attrs } : {}) }] }],
        { labelKey: 'map.edit.createPolygon', what: t('map.edit.createPolygon') },
      );
    }
  }, [refId, submit, parseAttributes, splitTarget, t, bump]);

  /* ─── 点击手势：create / delete / vertex-select / split ─── */
  useEffect(() => {
    const map = getMap();
    if (!map || !mapReady || !editTarget) return;
    const activeTools = ['draw_point', 'draw_line', 'draw_polygon', 'delete_feature', 'edit_vertices', 'split_feature'];
    if (!tool || !activeTools.includes(tool)) return;

    const onClick = async (e: MapMouseEvent) => {
      const active = tool;
      if (!active || !refId) return;
      const lngLat: [number, number] = [e.lngLat.lng, e.lngLat.lat];
      if (active === 'draw_point') {
        const attrs = parseAttributes();
        if (typeof attrs === 'string') { setStatus({ state: 'failed', message: attrs }); return; }
        await submit(
          [{ op: 'create_features', features: [{ geometry: { type: 'Point', coordinates: lngLat }, ...(Object.keys(attrs).length ? { properties: attrs } : {}) }] }],
          { labelKey: 'map.edit.createPoint', what: t('map.edit.createPoint') },
        );
        return;
      }
      if (active === 'draw_line' || active === 'draw_polygon' || active === 'split_feature') {
        // split 两阶段：未选目标时首击=选目标；已选目标后击点=画分割线
        if (active === 'split_feature' && !splitTarget && !draftRef.current?.points.length) {
          const picked = await pickEditTarget(lngLat, e.point);
          if (picked) { setSplitTarget(picked.id); return; }
          return;
        }
        if (active === 'split_feature' && !splitTarget) return;
        const draft = draftRef.current ?? { kind: active === 'draw_polygon' ? 'polygon' as const : active === 'draw_line' ? 'line' as const : 'split' as const, points: [] };
        // 双击完成时的重复点去抖（与 SketchEditor 同纪律）
        const last = draft.points[draft.points.length - 1];
        if (last) {
          const m = getMap();
          if (m) {
            const a = m.project(last); const b = m.project(lngLat);
            if (Math.hypot(a.x - b.x, a.y - b.y) < 4) return;
          }
        }
        draftRef.current = { kind: draft.kind, points: [...draft.points, lngLat] };
        bump();
        return;
      }
      if (active === 'delete_feature' || active === 'edit_vertices') {
        const picked = await pickEditTarget(lngLat, e.point);
        if (!picked) return;
        if (active === 'delete_feature') {
          await submit([{ op: 'delete_features' }], { featureIds: [picked.id], labelKey: 'map.edit.deleteFeature', what: t('map.edit.deleteFeature') });
        } else {
          setVertexTarget({ featureId: picked.id, geometry: picked.feature.geometry ?? null });
        }
        return;
      }
    };

    const onDblClick = (e: MapMouseEvent) => {
      if (draftRef.current) { e.preventDefault(); void completeDraft(); }
    };
    map.on('click', onClick);
    map.on('dblclick', onDblClick);
    return () => { map.off('click', onClick); map.off('dblclick', onDblClick); };
  }, [tool, mapReady, getMap, editTarget, refId, submit, parseAttributes, bump, t, splitTarget, completeDraft, pickEditTarget]);

  /* ─── 顶点拖拽（edit_vertices + vertexTarget）─── */
  useEffect(() => {
    const map = getMap();
    if (!map || !mapReady || tool !== 'edit_vertices' || !editTarget) return;
    let draggingIndex = -1;
    let geometryAtDragStart: GeoJSON.Geometry | null = null;

    const setVertex = (geometry: GeoJSON.Geometry, index: number, next: [number, number]): GeoJSON.Geometry | null => {
      if (geometry.type === 'LineString') {
        if (index < 0 || index >= geometry.coordinates.length) return null;
        const coordinates = geometry.coordinates.slice();
        coordinates[index] = next;
        return { type: 'LineString', coordinates };
      }
      if (geometry.type === 'Polygon') {
        const ring = geometry.coordinates[0];
        if (!ring || index < 0 || index >= ring.length) return null;
        const coordinates = geometry.coordinates.slice();
        const r = ring.slice();
        r[index] = next;
        if (index === 0) r[r.length - 1] = next;
        if (index === ring.length - 1) r[0] = next;
        coordinates[0] = r;
        return { type: 'Polygon', coordinates };
      }
      if (geometry.type === 'Point') {
        return { type: 'Point', coordinates: next };
      }
      return null;
    };

    const onDown = (e: MapMouseEvent & { features?: MapGeoJSONFeature[] }) => {
      if (!vertexTarget) return;
      const vertices = ringOf(vertexTarget.geometry);
      const m = getMap();
      if (!m) return;
      let hit = -1;
      for (let i = 0; i < vertices.length; i++) {
        const p = m.project(vertices[i]);
        if (Math.hypot(p.x - e.point.x, p.y - e.point.y) <= 8) { hit = i; break; }
      }
      if (hit < 0) return;
      draggingIndex = hit;
      geometryAtDragStart = vertexTarget.geometry;
      if (typeof m.dragPan?.disable === 'function') m.dragPan.disable();
    };
    const onMove = (e: MapMouseEvent) => {
      if (draggingIndex < 0 || !vertexTarget) return;
      // 逐帧只更新本地 draft（V04：mousemove 零 REST）
      const next = setVertex(vertexTarget.geometry, draggingIndex, [e.lngLat.lng, e.lngLat.lat]);
      if (next) setVertexTarget({ featureId: vertexTarget.featureId, geometry: next });
    };
    const onUp = async () => {
      if (draggingIndex < 0 || !vertexTarget) return;
      const target = vertexTarget;
      const before = geometryAtDragStart;
      draggingIndex = -1;
      geometryAtDragStart = null;
      const m = getMap();
      if (typeof m?.dragPan?.enable === 'function') m.dragPan.enable();
      if (before && JSON.stringify(before) !== JSON.stringify(target.geometry)) {
        await submit(
          [{ op: 'replace_geometry', geometry: target.geometry }],
          { featureIds: [target.featureId], labelKey: 'map.edit.editVertices', what: t('map.edit.editVertices') },
        );
      }
    };
    map.on('mousedown', DRAFT_POINT_LAYER, onDown);
    map.on('mousemove', onMove);
    map.on('mouseup', onUp);
    const onUpWindow = () => { void onUp(); };
    window.addEventListener('mouseup', onUpWindow);
    return () => {
      map.off('mousedown', DRAFT_POINT_LAYER, onDown);
      map.off('mousemove', onMove);
      map.off('mouseup', onUp);
      window.removeEventListener('mouseup', onUpWindow);
      if (typeof map.dragPan?.enable === 'function') map.dragPan.enable();
    };
  }, [tool, mapReady, getMap, editTarget, vertexTarget, submit, t]);

  /* ─── 键盘：Enter 完成 / Escape 取消（不落服务端写入） ─── */
  useEffect(() => {
    if (!editTarget) return;
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || target?.getAttribute('contenteditable') === 'true') return;
      if (e.key === 'Enter' && draftRef.current) {
        e.preventDefault();
        void completeDraft();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        if (draftRef.current) { draftRef.current = null; bump(); return; } // V05：取消草稿零写入
        if (vertexTarget) { setVertexTarget(null); return; }
        if (splitTarget) { setSplitTarget(null); return; }
        setActiveMapTool(null);
      }
    };
    const unregister = registerEscapeHandler(45, (event) => { onKeyDown(event); return event.defaultPrevented; });
    const nonEscape = (event: KeyboardEvent) => { if (event.key !== 'Escape') onKeyDown(event); };
    window.addEventListener('keydown', nonEscape);
    return () => { unregister(); window.removeEventListener('keydown', nonEscape); };
  }, [editTarget, completeDraft, vertexTarget, splitTarget, setActiveMapTool, bump]);

  /* ─── merge：按当前 selection（≥2）提交 ─── */
  const mergeSelection = useCallback(async () => {
    const ids = selection?.selected_ids?.map(String) ?? [];
    if (!refId || ids.length < 2) return;
    await submit(
      [{ op: 'merge_features', field_policy: { mode: 'first' } }],
      { featureIds: ids, labelKey: 'map.edit.mergeFeatures', what: t('map.edit.mergeFeatures') },
    );
  }, [selection, refId, submit, t]);

  if (!editTarget || !targetLayer) return null;

  const mergeableCount = selection?.selected_ids?.length ?? 0;
  const drawing = tool === 'draw_point' || tool === 'draw_line' || tool === 'draw_polygon' || tool === 'split_feature';

  return (
    <div
      data-testid="feature-edit-panel"
      className="pointer-events-auto absolute bottom-3 left-1/2 z-30 flex -translate-x-1/2 flex-col gap-1.5 rounded-md border border-edge-subtle bg-surface-raised/95 px-3 py-2 text-micro text-ink shadow-agent-md backdrop-blur-md"
    >
      <div className="flex items-center gap-2">
        <span className="font-medium">{t('map.edit.editing')}</span>
        <span className="max-w-40 truncate text-ink-secondary" data-testid="feature-edit-target-name">{targetLayer.name}</span>
        <button
          type="button"
          className="rounded-xs border border-edge-subtle px-2 py-0.5 text-ink-secondary hover:bg-surface-hover"
          onClick={() => { setEditTarget(null); setVertexTarget(null); setSplitTarget(null); draftRef.current = null; }}
        >
          {t('map.edit.stop')}
        </button>
      </div>
      {drawing && (
        <div className="flex items-center gap-2">
          <input
            data-testid="feature-edit-attrs"
            className="w-56 rounded-xs border border-edge-subtle bg-surface px-2 py-0.5"
            placeholder={t('map.edit.attrsPlaceholder')}
            value={attributesJson}
            onChange={(e) => setAttributesJson(e.target.value)}
          />
          {tool === 'split_feature' && (
            <span className="text-ink-secondary" data-testid="feature-edit-split-hint">
              {splitTarget ? t('map.edit.splitDrawLine') : t('map.edit.splitPickTarget')}
            </span>
          )}
        </div>
      )}
      <div className="flex items-center gap-2">
        <button
          type="button"
          data-testid="feature-edit-merge"
          disabled={mergeableCount < 2 || status.state === 'submitting'}
          className="rounded-xs bg-status-accent px-2 py-0.5 font-medium text-ink-on-accent hover:opacity-90 disabled:opacity-40"
          onClick={() => { void mergeSelection(); }}
        >
          {t('map.edit.mergeSelected', { count: mergeableCount })}
        </button>
        <span data-testid="feature-edit-status" className="text-ink-secondary">
          {status.state === 'submitting' && `${t('map.edit.submitting')} ${status.what}…`}
          {status.state === 'failed' && `${t('map.edit.failed')}: ${status.message}`}
          {status.state === 'done' && `${status.what} ${t('map.edit.saved')}`}
          {status.state === 'idle' && ''}
        </span>
      </div>
    </div>
  );
}

export default FeatureLayerEditor;
