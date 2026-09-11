'use client';

/**
 * SketchEditor — V7 草图编辑交互面（挂载在 <Map> 内）。
 *
 * 边界：
 * - 草图几何真相在 lib/edit/sketch-store（模块 store，逐帧顶点拖拽不进
 *   zustand）；工具激活态在 toolSlice（全局互斥）；本组件只做「地图交互
 *   ↔ store」的双向缝合；
 * - 渲染走自管 source/layers（`wb-sketch*`），不经 MapSpecRuntime —— 草图
 *   是用户 transient 创作物，不进权威 MapSpec、不产生 user mutation；
 * - 图层树行（wb-sketch）无 `_mapspecLayerId`：对该行的显隐/不透明度在
 *   HUD 本地生效（本组件回读行状态应用到自管图层；user-mutation 对无
 *   spec 绑定的行短路本地通道，不发服务端 mutation），删除同理走本地
 *   通道 —— 图层树行的可见性/计数由行 source 同步保持；
 * - 所有几何变更经 recordCommand 进 workbench undo 栈（可逆编辑纪律）。
 */
import React, { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import type { MapRef } from 'react-map-gl/maplibre';
import type { MapGeoJSONFeature, MapMouseEvent } from 'maplibre-gl';
import { useHudStore } from '@/lib/store/useHudStore';
import { recordCommand } from '@/lib/workbench/undo';
import {
  getSketchState,
  nextSketchId,
  replaceSketchFeatures,
  resetSketchStore,
  setSketchDraft,
  setSketchSelected,
  sketchFeatureCollection,
  subscribeSketch,
  getSketchSnapshot,
  SKETCH_LAYER_ID,
  SKETCH_SOURCE_ID,
  updateSketchGeometry,
  type SketchFeature,
} from '@/lib/edit/sketch-store';
import {
  closeRing,
  draftToFeature,
  geometryVertices,
  snapToVertex,
} from '@/lib/edit/sketch-geometry';
import { useT } from '@/lib/i18n/useT';
import { t as tNow } from '@/lib/i18n/t';

const SKETCH_FILL_LAYER = 'wb-sketch-fill';
const SKETCH_LINE_LAYER = 'wb-sketch-line';
const SKETCH_VERTEX_LAYER = 'wb-sketch-vertex';
const SKETCH_DRAFT_LAYER = 'wb-sketch-draft';

const SKETCH_COLOR = '#38bdf8';

/** 草图源数据：要素 + 草稿（虚线渲染）+ 选中要素顶点。 */
function buildSketchData(): GeoJSON.FeatureCollection<GeoJSON.Geometry, Record<string, unknown>> {
  const { features, draft, selectedFeatureId } = getSketchState();
  const out: GeoJSON.FeatureCollection<GeoJSON.Geometry, Record<string, unknown>> = {
    type: 'FeatureCollection',
    // fid 进 properties：maplibre 对 GeoJSON 顶层 id 的透传不可靠，
    // queryRenderedFeatures → 要素回查必须走 properties。
    features: features.map((f) => ({ ...f, properties: { ...f.properties, meta: 'feature', fid: f.id } })),
  };
  if (draft) {
    const coords = draft.coordinates;
    if (draft.kind === 'polygon' && coords.length >= 3) {
      out.features.push({
        id: 'wb-sketch-draft',
        type: 'Feature',
        geometry: { type: 'Polygon', coordinates: [closeRing(coords)] },
        properties: { meta: 'draft' },
      } as never);
    }
    if (coords.length >= 2) {
      out.features.push({
        id: 'wb-sketch-draft-line',
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: coords },
        properties: { meta: 'draft' },
      } as never);
    }
    for (const c of coords) {
      out.features.push({
        id: `wb-sketch-draft-v-${c[0]}-${c[1]}`,
        type: 'Feature',
        geometry: { type: 'Point', coordinates: c },
        properties: { meta: 'draft-vertex' },
      } as never);
    }
  }
  const selected = features.find((f) => f.id === selectedFeatureId);
  if (selected) {
    for (const c of geometryVertices(selected.geometry)) {
      out.features.push({
        id: `wb-sketch-v-${c[0]}-${c[1]}`,
        type: 'Feature',
        geometry: { type: 'Point', coordinates: c },
        properties: { meta: 'vertex', parent: selected.id },
      } as never);
    }
  }
  return out;
}

/** 确保图层树里有草图行（首个要素前即建，显隐/不透明度先于绘制可用）。 */
function ensureSketchLayerRow(): void {
  const store = useHudStore.getState();
  if (store.layers.some((l) => l.id === SKETCH_LAYER_ID)) return;
  store.addLayer({
    id: SKETCH_LAYER_ID,
    name: '草图图层',
    type: 'vector',
    visible: true,
    opacity: 1,
    source: sketchFeatureCollection(),
    style: { color: SKETCH_COLOR },
  });
}

function syncSketchLayerRow(): void {
  const store = useHudStore.getState();
  if (!store.layers.some((l) => l.id === SKETCH_LAYER_ID)) return;
  store.updateLayer(SKETCH_LAYER_ID, {
    source: sketchFeatureCollection(),
  }, { source: 'server' });
}

/** 命令包装：捕获前后要素集，进 workbench undo 栈（重放后同步图层树行）。 */
function commitSketchCommand(
  label: string,
  previous: SketchFeature[],
  mutate: () => void,
): void {
  const before = previous;
  mutate();
  const after = getSketchState().features;
  recordCommand({
    label,
    kind: 'sketch',
    actor: 'user',
    layerIds: [SKETCH_LAYER_ID],
    undo: () => {
      replaceSketchFeatures(before);
      syncSketchLayerRow();
    },
    redo: () => {
      replaceSketchFeatures(after);
      syncSketchLayerRow();
    },
  });
}

export function SketchEditor({ mapRef }: { mapRef: React.RefObject<MapRef | null> }) {
  const t = useT();
  const mapReady = useHudStore((s) => s.mapLoaded);
  const tool = useHudStore((s) => s.activeMapTool);
  const snapping = useHudStore((s) => s.snappingEnabled);
  const sketchVisible = useHudStore((s) => s.layers?.find((l) => l.id === SKETCH_LAYER_ID)?.visible !== false);
  const sketchOpacity = useHudStore((s) => s.layers?.find((l) => l.id === SKETCH_LAYER_ID)?.opacity ?? 1);
  const setSketchDirty = useHudStore((s) => s.setSketchDirty);
  const sketchDirty = useHudStore((s) => s.sketchDirty);
  const setActiveMapTool = useHudStore((s) => s.setActiveMapTool);
  const sketchVersion = useSyncExternalStore(subscribeSketch, getSketchSnapshot);
  const sketch = useMemo(
    () => getSketchState(),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- version drives the re-read
    [sketchVersion],
  );
  const draftRef = useRef(sketch.draft);
  draftRef.current = sketch.draft;
  const toolRef = useRef(tool);
  toolRef.current = tool;
  const snappingRef = useRef(snapping);
  snappingRef.current = snapping;

  const getMap = useCallback(() => mapRef.current?.getMap() ?? null, [mapRef]);

  /* ─── 源/图层挂载 + 数据同步 ─── */
  useEffect(() => {
    const map = getMap();
    if (!map || !mapReady) return;
    if (!map.getSource(SKETCH_SOURCE_ID)) {
      map.addSource(SKETCH_SOURCE_ID, { type: 'geojson', data: buildSketchData() });
    }
    const layer = (id: string) => map.getLayer?.(id);
    if (!layer(SKETCH_FILL_LAYER)) {
      map.addLayer({
        id: SKETCH_FILL_LAYER,
        type: 'fill',
        source: SKETCH_SOURCE_ID,
        filter: ['==', ['get', 'meta'], 'feature'],
        paint: { 'fill-color': SKETCH_COLOR, 'fill-opacity': 0.18 },
      });
    }
    if (!layer(SKETCH_LINE_LAYER)) {
      map.addLayer({
        id: SKETCH_LINE_LAYER,
        type: 'line',
        source: SKETCH_SOURCE_ID,
        filter: ['==', ['get', 'meta'], 'feature'],
        paint: { 'line-color': SKETCH_COLOR, 'line-width': 2 },
      });
    }
    if (!layer(SKETCH_DRAFT_LAYER)) {
      map.addLayer({
        id: SKETCH_DRAFT_LAYER,
        type: 'line',
        source: SKETCH_SOURCE_ID,
        filter: ['==', ['get', 'meta'], 'draft'],
        paint: { 'line-color': SKETCH_COLOR, 'line-width': 2, 'line-dasharray': [2, 2] },
      });
    }
    if (!layer(SKETCH_VERTEX_LAYER)) {
      map.addLayer({
        id: SKETCH_VERTEX_LAYER,
        type: 'circle',
        source: SKETCH_SOURCE_ID,
        filter: ['in', ['get', 'meta'], ['literal', ['vertex', 'draft-vertex']]],
        paint: {
          'circle-radius': 5,
          'circle-color': SKETCH_COLOR,
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 1.5,
        },
      });
    }
    const source = map.getSource(SKETCH_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
    source?.setData(buildSketchData());
    // 显隐/不透明度：图层树行 → 自管图层（行是用户通道，这里是渲染执行）。
    const visibility = sketchVisible ? 'visible' : 'none';
    for (const id of [SKETCH_FILL_LAYER, SKETCH_LINE_LAYER, SKETCH_VERTEX_LAYER, SKETCH_DRAFT_LAYER]) {
      if (layer(id)) map.setLayoutProperty(id, 'visibility', visibility);
    }
    if (layer(SKETCH_FILL_LAYER)) map.setPaintProperty(SKETCH_FILL_LAYER, 'fill-opacity', 0.18 * sketchOpacity);
    if (layer(SKETCH_LINE_LAYER)) map.setPaintProperty(SKETCH_LINE_LAYER, 'line-opacity', sketchOpacity);
    if (layer(SKETCH_DRAFT_LAYER)) map.setPaintProperty(SKETCH_DRAFT_LAYER, 'line-opacity', sketchOpacity);
    if (layer(SKETCH_VERTEX_LAYER)) map.setPaintProperty(SKETCH_VERTEX_LAYER, 'circle-opacity', sketchOpacity);
  }, [mapReady, getMap, sketchVersion, sketchVisible, sketchOpacity]);

  /* ─── 完成草稿（Enter / 双击）─── */
  const completeDraft = useCallback(() => {
    const draft = draftRef.current;
    if (!draft) return;
    const previous = [...getSketchState().features];
    const feature = draftToFeature(draft.kind, draft.coordinates, nextSketchId());
    setSketchDraft(null);
    if (!feature) return;
    ensureSketchLayerRow();
    commitSketchCommand(
      draft.kind === 'polygon' ? '绘制多边形' : '绘制线',
      previous,
      () => replaceSketchFeatures([...previous, feature]),
    );
    syncSketchLayerRow();
    setSketchDirty(true);
  }, [setSketchDirty]);

  /* ─── 地图点击：绘制 / 删除 / 选中 ─── */
  useEffect(() => {
    const map = getMap();
    if (!map || !mapReady) return;
    if (!tool || !(tool === 'draw_point' || tool === 'draw_line' || tool === 'draw_polygon'
      || tool === 'delete_feature' || tool === 'edit_vertices')) return;

    const onClick = (e: MapMouseEvent) => {
      const active = toolRef.current;
      if (!active) return;
      const lngLat: [number, number] = [e.lngLat.lng, e.lngLat.lat];

      if (active === 'draw_point') {
        const previous = [...getSketchState().features];
        const feature: SketchFeature = {
          id: nextSketchId(),
          type: 'Feature',
          geometry: { type: 'Point', coordinates: lngLat },
          properties: { kind: 'sketch_point' },
        };
        ensureSketchLayerRow();
        commitSketchCommand('绘制点', previous, () => replaceSketchFeatures([...previous, feature]));
        syncSketchLayerRow();
        setSketchDirty(true);
        return;
      }

      if (active === 'draw_line' || active === 'draw_polygon') {
        const draft = draftRef.current ?? { kind: active === 'draw_polygon' ? 'polygon' as const : 'line' as const, coordinates: [] };
        // 双击完成时 maplibre 仍派发两次 click —— 与上一点像素距离过近的
        // 点击忽略，避免草稿尾部进入重复顶点（closeRing 不去重）。
        const last = draft.coordinates[draft.coordinates.length - 1];
        if (last) {
          const mapForDedupe = getMap();
          if (mapForDedupe) {
            const a = mapForDedupe.project(last);
            const b = mapForDedupe.project(lngLat);
            if (Math.hypot(a.x - b.x, a.y - b.y) < 4) return;
          }
        }
        // 吸附：已有要素顶点 + 草图首点（闭环）
        let coordinate = lngLat;
        if (snappingRef.current) {
          const map2 = getMap();
          if (map2) {
            const candidates = getSketchState().features.flatMap((f) => geometryVertices(f.geometry));
            const d = draftRef.current;
            if (d && d.coordinates.length > 0) candidates.push(d.coordinates[0]);
            const hit = snapToVertex(
              lngLat,
              candidates,
              (p) => map2.project(p),
              (p) => map2.unproject([p.x, p.y]),
            );
            if (hit) coordinate = hit.coordinate;
          }
        }
        setSketchDraft({ ...draft, coordinates: [...draft.coordinates, coordinate] });
        return;
      }

      if (active === 'delete_feature') {
        const hits = map.queryRenderedFeatures(e.point, {
          layers: [SKETCH_FILL_LAYER, SKETCH_LINE_LAYER].filter((id) => map.getLayer?.(id)),
        }) as MapGeoJSONFeature[];
        const targetId = String((hits[0]?.properties?.fid as string | undefined) ?? '');
        if (!targetId) return;
        const state = getSketchState();
        const target = state.features.find((f) => f.id === targetId);
        if (!target) return;
        const previous = [...state.features];
        ensureSketchLayerRow();
        commitSketchCommand('删除草图要素', previous, () =>
          replaceSketchFeatures(previous.filter((f) => f.id !== targetId)));
        if (state.selectedFeatureId === targetId) setSketchSelected(null);
        syncSketchLayerRow();
        setSketchDirty(true);
        return;
      }

      if (active === 'edit_vertices') {
        const hits = map.queryRenderedFeatures(e.point, {
          layers: [SKETCH_FILL_LAYER, SKETCH_LINE_LAYER].filter((id) => map.getLayer?.(id)),
        }) as MapGeoJSONFeature[];
        const targetId = String((hits[0]?.properties?.fid as string | undefined) ?? '');
        setSketchSelected(targetId
          && getSketchState().features.some((f) => f.id === targetId) ? targetId : null);
      }
    };

    const onDblClick = (e: MapMouseEvent) => {
      if (draftRef.current) {
        e.preventDefault();
        completeDraft();
      }
    };

    map.on('click', onClick);
    map.on('dblclick', onDblClick);
    return () => {
      map.off('click', onClick);
      map.off('dblclick', onDblClick);
    };
  }, [tool, mapReady, getMap, completeDraft, setSketchDirty]);

  /* ─── 顶点拖拽（edit_vertices + 已选中要素）─── */
  useEffect(() => {
    const map = getMap();
    if (!map || !mapReady || tool !== 'edit_vertices') return;
    let dragging: { parentId: string; vertexIndex: number } | null = null;
    // undo 粒度 = 一次拖拽手势：down 时快照几何，up 时以「快照 ↔ 终态」
    // 入栈（逐帧中间态不进 undo 栈）。
    const dragStartGeometry = new Map<string, GeoJSON.Geometry>();

    const onVertexDown = (e: MapMouseEvent & { features?: MapGeoJSONFeature[] }) => {
      const props = e.features?.[0]?.properties;
      if (!props || props.meta !== 'vertex') return;
      const parentId = String(props.parent ?? '');
      const state = getSketchState();
      const feature = state.features.find((f) => f.id === parentId);
      if (!feature) return;
      const vertices = geometryVertices(feature.geometry);
      const coord: [number, number] = [e.lngLat.lng, e.lngLat.lat];
      const hit = snapToVertex(coord, vertices, (p) => map.project(p), (p) => map.unproject([p.x, p.y]), 14);
      let vertexIndex = -1;
      if (hit) {
        vertexIndex = vertices.findIndex(
          (v) => v[0] === hit.coordinate[0] && v[1] === hit.coordinate[1],
        );
      }
      if (vertexIndex < 0) return;
      // 快照紧贴手势确认（vertexIndex 命中）之后 —— 未命中的提前 return
      // 不留脏快照（review MINOR-6）。
      dragStartGeometry.set(parentId, feature.geometry);
      dragging = { parentId, vertexIndex };
      if (typeof map.dragPan?.disable === 'function') map.dragPan.disable();
    };

    const onMove = (e: MapMouseEvent) => {
      if (!dragging) return;
      const state = getSketchState();
      const feature = state.features.find((f) => f.id === dragging!.parentId);
      if (!feature) return;
      const geometry = moveVertex(feature.geometry, dragging.vertexIndex, [e.lngLat.lng, e.lngLat.lat]);
      if (geometry) updateSketchGeometry(feature.id, geometry);
    };

    const onUp = () => {
      if (!dragging) return;
      const done = dragging;
      dragging = null;
      if (typeof map.dragPan?.enable === 'function') map.dragPan.enable();
      const state = getSketchState();
      const feature = state.features.find((f) => f.id === done.parentId);
      if (!feature) return;
      const before = dragStartGeometry.get(done.parentId);
      const after = feature.geometry;
      if (before && JSON.stringify(before) !== JSON.stringify(after)) {
        recordCommand({
          label: t('map.sketch.editVertices'),
          kind: 'sketch',
          actor: 'user',
          layerIds: [SKETCH_LAYER_ID],
          undo: () => {
            updateSketchGeometry(done.parentId, before);
            syncSketchLayerRow();
          },
          redo: () => {
            updateSketchGeometry(done.parentId, after);
            syncSketchLayerRow();
          },
        });
      }
      dragStartGeometry.delete(done.parentId);
      syncSketchLayerRow();
      setSketchDirty(true);
    };

    map.on('mousedown', SKETCH_VERTEX_LAYER, onVertexDown);
    map.on('mousemove', onMove);
    map.on('mouseup', onUp);
    // 拖拽期间鼠标离开画布也要收尾
    const onMouseUpWindow = () => onUp();
    window.addEventListener('mouseup', onMouseUpWindow);
    return () => {
      map.off('mousedown', SKETCH_VERTEX_LAYER, onVertexDown);
      map.off('mousemove', onMove);
      map.off('mouseup', onUp);
      window.removeEventListener('mouseup', onMouseUpWindow);
      if (typeof map.dragPan?.enable === 'function') map.dragPan.enable();
    };
  }, [tool, mapReady, getMap, setSketchDirty]);

  /* ─── 键盘：Enter 完成 / Escape 取消草稿或退出工具 ─── */
  useEffect(() => {
    if (!tool || !(tool === 'draw_line' || tool === 'draw_polygon'
      || tool === 'draw_point' || tool === 'delete_feature' || tool === 'edit_vertices')) return;
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || target?.getAttribute('contenteditable') === 'true') return;
      if (e.key === 'Enter' && draftRef.current) {
        e.preventDefault();
        completeDraft();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        if (draftRef.current) setSketchDraft(null);
        else setActiveMapTool(null);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [tool, completeDraft, setActiveMapTool]);

  /* ─── 工具光标反馈 ─── */
  useEffect(() => {
    const map = getMap();
    if (!map || !mapReady) return;
    const canvas = typeof map.getCanvas === 'function' ? map.getCanvas() : null;
    if (!canvas?.style) return;
    const sketchTools = ['draw_point', 'draw_line', 'draw_polygon', 'delete_feature', 'edit_vertices'];
    if (tool && sketchTools.includes(tool)) {
      canvas.style.cursor = tool === 'edit_vertices' ? 'move' : 'crosshair';
    } else {
      canvas.style.cursor = '';
    }
  }, [tool, mapReady, getMap]);

  /* ─── 会话/卸载清场：草稿不残留 ─── */
  useEffect(() => () => {
    if (draftRef.current) setSketchDraft(null);
  }, []);

  if (!sketchDirty) return null;

  return (
    <div
      data-testid="sketch-save-bar"
      role="status"
      aria-live="polite"
      className="pointer-events-auto absolute bottom-3 left-3 z-30 flex items-center gap-2 rounded-md border border-edge-subtle bg-surface-raised/95 px-2.5 py-1.5 text-micro text-ink shadow-agent-md backdrop-blur-md"
    >
      <span>{t('map.sketch.unsaved')}</span>
      <button
        type="button"
        className="rounded-xs bg-status-accent px-2 py-0.5 font-medium text-ink-on-accent hover:opacity-90"
        onClick={() => {
          const data = sketchFeatureCollection();
          const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/geo+json' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `sketch-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.geojson`;
          a.click();
          URL.revokeObjectURL(url);
          setSketchDirty(false);
          useHudStore.getState().pushOpLog({
            id: `op-${Date.now()}`,
            type: 'add',
            label: t('map.sketch.exportGeoJson'),
            time: new Date().toLocaleTimeString('zh-CN'),
            actor: 'user',
          });
        }}
      >
        {t('map.sketch.save')}
      </button>
      <button
        type="button"
        className="rounded-xs border border-edge-subtle px-2 py-0.5 text-ink-secondary hover:bg-surface-hover"
        onClick={() => {
          resetSketchStore();
          setSketchSelected(null);
          setSketchDirty(false);
          journalSketchDiscard();
        }}
      >
        {t('map.sketch.discard')}
      </button>
    </div>
  );
}

function journalSketchDiscard(): void {
  // 丢弃是显式破坏性确认 —— 仅入 journal（不可逆），不进 undo 栈。
  import('@/lib/workbench/undo').then(({ journalOnly }) => {
    journalOnly({ type: 'remove', label: tNow('map.sketch.discardTitle'), actor: 'user' });
  }).catch(() => { /* noop */ });
}

/** 在几何的第 index 个顶点位置替换坐标（LineString/Polygon 环 / Point 不适用）。 */
function moveVertex(geometry: GeoJSON.Geometry, index: number, next: [number, number]): GeoJSON.Geometry | null {
  if (geometry.type === 'LineString') {
    if (index < 0 || index >= geometry.coordinates.length) return null;
    const coordinates = geometry.coordinates.slice();
    coordinates[index] = next;
    return { type: 'LineString', coordinates };
  }
  if (geometry.type === 'Polygon') {
    const ring = geometry.coordinates[0];
    if (!ring || index < 0 || index >= ring.length) return null;
    const coordinates = ring.slice();
    coordinates[index] = next;
    // 拖动首/尾点：同步闭合端，保持环闭合
    if (index === 0) coordinates[coordinates.length - 1] = next;
    if (index === ring.length - 1) coordinates[0] = next;
    return { type: 'Polygon', coordinates: [coordinates, ...geometry.coordinates.slice(1)] };
  }
  return null;
}

export default SketchEditor;
