import type { MapSpec, MapSpecLayer } from '@/lib/mapspec-compiler/types';
import { hudStateToMapSpec, type HudToSpecInput } from '@/lib/mapspec-runtime/adapter';

export type PendingPresentation = Record<string, { visible?: boolean; opacity?: number }>;

function applyPending(layer: MapSpecLayer, pending: PendingPresentation): MapSpecLayer {
  const id = String(layer.id || '');
  const parent = id.includes('__') ? id.split('__')[0] : id;
  const patch = pending[id] || pending[parent];
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
    const paint: Record<string, unknown> = { ...(next.paint || {}) };
    paint.opacity = patch.opacity;
    const key = {
      circle: 'circle-opacity',
      fill: 'fill-opacity',
      line: 'line-opacity',
      raster: 'raster-opacity',
      heatmap: 'heatmap-opacity',
      'fill-extrusion': 'fill-extrusion-opacity',
    }[next.type];
    if (key) paint[key] = patch.opacity;
    next.paint = paint;
  }
  return next;
}

function layerAliases(id: string): string[] {
  const parent = id.includes('__') ? id.split('__')[0] : id;
  return parent === id ? [id] : [id, parent];
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
  return merged as MapSpec['sources'][string];
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
  return sources;
}

/** Live reconcile input: committed MapSpec + pending overlay. HUD is not Desired. */
export function composeLiveMapSpec(
  committed: MapSpec | null | undefined,
  hud: HudToSpecInput,
  pending: PendingPresentation = {},
  removed: string[] = [],
): MapSpec {
  const hudSpec = hudStateToMapSpec(hud);
  if (!committed) return hudSpec;

  const layers = (committed.layers || [])
    .filter((layer) => !isPendingRemoved(layer, removed))
    .map((layer) => applyPending(
      { ...layer, layout: { ...layer.layout }, paint: { ...layer.paint } },
      pending,
    ));

  return {
    ...committed,
    sources: mergeHudSources(committed, hud, hudSpec),
    layers,
  };
}
