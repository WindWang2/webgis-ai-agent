import type { MapSpec, MapSpecLayer, MapSpecLayerPaint } from '@/lib/mapspec-compiler/types';
import { hudStateToMapSpec, type HudToSpecInput } from '@/lib/mapspec-runtime/adapter';

export type PendingPresentation = Record<string, { visible?: boolean; opacity?: number }>;

function applyPending(layer: MapSpecLayer, pending: PendingPresentation): MapSpecLayer {
  const id = String(layer.id || '');
  const aliases = layerAliases(id);
  let patch: { visible?: boolean; opacity?: number } | undefined;
  for (const alias of aliases) {
    if (pending[alias]) {
      patch = pending[alias];
      break;
    }
  }
  if (!patch) return layer;
  const next: MapSpecLayer = {
    ...layer,
    layout: { ...layer.layout },
    paint: { ...layer.paint },
  };
  if (patch.visible !== undefined) {
    next.layout = {
      ...next.layout,
      visibility: patch.visible ? 'visible' : 'none',
    };
  }
  if (patch.opacity !== undefined) {
    const paint: MapSpecLayerPaint = { ...(next.paint || {}) };
    paint.opacity = patch.opacity;
    const key = (
      next.type === 'symbol'
        ? undefined
        : ({
            circle: 'circle-opacity',
            fill: 'fill-opacity',
            line: 'line-opacity',
            raster: 'raster-opacity',
            heatmap: 'heatmap-opacity',
            'fill-extrusion': 'fill-extrusion-opacity',
          } as const)[next.type]
    );
    if (key) paint[key] = patch.opacity;
    next.paint = paint;
  }
  return next;
}

function layerAliases(id: string): string[] {
  const aliases = new Set<string>([id]);
  if (id.includes('__')) {
    aliases.add(id.split('__')[0]);
  }
  if (id.startsWith('custom-')) {
    const withoutCustom = id.slice(7);
    aliases.add(withoutCustom);
    if (withoutCustom.includes('__')) {
      aliases.add(withoutCustom.split('__')[0]);
    }
  }
  return Array.from(aliases);
}

function isPendingRemoved(layer: MapSpecLayer, removed: string[]): boolean {
  if (removed.length === 0) return false;
  return layerAliases(String(layer.id || '')).some((alias) => removed.includes(alias));
}

function exclusiveGeojsonPayload(
  base: MapSpec['sources'][string] | undefined,
  overlay: MapSpec['sources'][string],
): MapSpec['sources'][string] {
  const merged = { ...(base || {}), ...overlay } as Record<string, unknown>;
  if (overlay && typeof overlay === 'object') {
    if ('inlineData' in overlay && overlay.inlineData != null) {
      delete merged.url;
      delete merged.dataPath;
    } else if ('url' in overlay && overlay.url != null) {
      delete merged.inlineData;
      delete merged.dataPath;
    } else if ('dataPath' in overlay && overlay.dataPath != null) {
      delete merged.inlineData;
      delete merged.url;
    }
  }
  return merged as unknown as MapSpec['sources'][string];
}

function mergeHudSources(
  committed: MapSpec,
  hud: HudToSpecInput,
  hudSpec: MapSpec,
): MapSpec['sources'] {
  const sources: MapSpec['sources'] = { ...(committed.sources || {}) };
  const mergeInto = (id: string | undefined, source: MapSpec['sources'][string]) => {
    if (!id) return;
    sources[id] = exclusiveGeojsonPayload(sources[id], source);
  };

  for (const [id, source] of Object.entries(hudSpec.sources || {})) {
    mergeInto(id, source);
  }

  for (const layer of hud.layers || []) {
    const hudSource = hudSpec.sources?.[layer.id];
    if (!hudSource) continue;
    const specId = layer._mapspecLayerId;
    mergeInto(specId, hudSource);
    for (const specLayer of committed.layers || []) {
      const aliases = layerAliases(String(specLayer.id || ''));
      if (specId && aliases.includes(specId)) {
        mergeInto(String(specLayer.source || specId), hudSource);
      }
    }
  }

  // ref 数据身份合并：后端直写图层（webgis_map_product 等）的源只带
  // ref_id，其数据 ref 往往正是某个 HUD 图层已拉取的同一份（POI 查询
  // 结果）。按 ref 身份并入，product 图层无需二次下载即可挂载。
  // 只认已落地的 GeoJSON 载荷（inlineData 非空占位）——空占位（HUD
  // 尚未拉回）与 MVT 矢量源不参与。
  const hudRefPayloads = new Map<string, NonNullable<MapSpec['sources'][string]>>();
  for (const layer of hud.layers || []) {
    const refId = layer._refId;
    const data = (hudSpec.sources?.[layer.id] as unknown as Record<string, unknown> | undefined)?.inlineData as
      | { features?: unknown[] }
      | undefined;
    if (!refId || !data || !Array.isArray(data.features) || data.features.length === 0) continue;
    if (!hudRefPayloads.has(refId)) {
      hudRefPayloads.set(refId, hudSpec.sources![layer.id]);
    }
  }
  if (hudRefPayloads.size > 0) {
    for (const [sid, source] of Object.entries(sources)) {
      const s = source as unknown as Record<string, unknown> | undefined;
      if (!s || s.type !== 'geojson') continue;
      if (s.inlineData != null || s.url != null || s.dataPath != null) continue;
      const refId = typeof s.ref_id === 'string' ? s.ref_id : typeof s.ref === 'string' ? s.ref : null;
      const payload = refId ? hudRefPayloads.get(refId) : undefined;
      if (payload) {
        sources[sid] = exclusiveGeojsonPayload(source, payload);
      }
    }
  }
  return sources;
}

/** Live reconcile input: committed MapSpec + pending overlay. HUD is not Desired. */
export function composeLiveMapSpec(
  committed: MapSpec | null | undefined,
  hud: HudToSpecInput,
  pending: PendingPresentation = {},
  removed: string[] = [],
): MapSpec {
  // #1078(G-5): 输入身份 memo —— reconcile effect 一次事件典型重跑 2-3 次
  // 且多数输入未变；同输入返回同一对象让 runtime 的对象身份 no-op 门
  // （同步路径既有）在异步路径同样生效，等价重复 compose 不再触发
  // worker diff。hud.layers/processLayers/pending/removed 都是引用稳定
  // 输入（zustand 切片与 session-cursor 模块状态只在变更时换引用）。
  if (
    composeMemo.committed === committed
    && composeMemo.hudLayers === hud.layers
    && composeMemo.hudProcess === hud.processLayers
    && composeMemo.pending === pending
    && composeMemo.removed === removed
    && composeMemo.hudFilters === hud.activeFilters
    && composeMemo.hudSelectionFilters === hud.selectionFilters
    && composeMemo.hud3D === hud.is3D
    && composeMemo.result != null
  ) {
    return composeMemo.result;
  }
  const hudSpec = hudStateToMapSpec(hud);
  if (!committed) {
    composeMemo.result = hudSpec;
    composeMemo.committed = null;
    composeMemo.hudLayers = hud.layers;
    composeMemo.hudProcess = hud.processLayers;
    composeMemo.pending = pending;
    composeMemo.removed = removed;
    composeMemo.hudFilters = hud.activeFilters;
    // V4 review 修复：null-committed 分支漏存 selectionFilters —— A→B→A
    // 翻转可命中带 stale selection 的 memo（选择过滤恰是 V4 高频翻转面）。
    composeMemo.hudSelectionFilters = hud.selectionFilters;
    composeMemo.hud3D = hud.is3D;
    return hudSpec;
  }

  const committedLayers = (committed.layers || [])
    .filter((layer) => !isPendingRemoved(layer, removed))
    .map((layer) => applyPending(
      { ...layer, layout: { ...layer.layout }, paint: { ...layer.paint } },
      pending,
    ));

  // Build a set of layer IDs already covered in committed
  const committedIdSet = new Set<string>();
  for (const l of committedLayers) {
    if (l.id) {
      committedIdSet.add(l.id);
      for (const alias of layerAliases(l.id)) {
        committedIdSet.add(alias);
      }
    }
  }

  // Also include HUD-only layers (such as active spatial analysis results, POI queries,
  // heatmaps, and user layers) that are not shadowed by committed spec layers
  const hudOnlyLayers: MapSpecLayer[] = [];
  for (const hl of hudSpec.layers || []) {
    if (!hl.id || isPendingRemoved(hl, removed)) continue;
    const baseId = hl.id.split('__')[0];
    if (!committedIdSet.has(hl.id) && !committedIdSet.has(baseId)) {
      hudOnlyLayers.push(applyPending(hl, pending));
    }
  }

  const result: MapSpec = {
    ...committed,
    sources: mergeHudSources(committed, hud, hudSpec),
    layers: [...committedLayers, ...hudOnlyLayers],
  };
  composeMemo.result = result;
  composeMemo.committed = committed;
  composeMemo.hudLayers = hud.layers;
  composeMemo.hudProcess = hud.processLayers;
  composeMemo.pending = pending;
  composeMemo.removed = removed;
  composeMemo.hudFilters = hud.activeFilters;
  composeMemo.hudSelectionFilters = hud.selectionFilters;
  composeMemo.hud3D = hud.is3D;
  return result;
}

const composeMemo: {
  committed: MapSpec | null | undefined;
  hudLayers: unknown;
  hudProcess: unknown;
  pending: PendingPresentation | undefined;
  removed: string[] | undefined;
  hudFilters: unknown;
  hudSelectionFilters: unknown;
  hud3D: unknown;
  result: MapSpec | null;
} = {
  committed: undefined, hudLayers: undefined, hudProcess: undefined,
  pending: undefined, removed: undefined, hudFilters: undefined,
  hudSelectionFilters: undefined, hud3D: undefined, result: null,
};
