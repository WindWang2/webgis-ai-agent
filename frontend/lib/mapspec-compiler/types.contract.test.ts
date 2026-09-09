/**
 * V6（ADR-0120 W3）：TS 类型面契约。
 *
 * generated 脊柱经 types.ts re-export 后必须保持既有编译兼容面：
 * - 既有形状（V5 语料形状）仍 satisfies MapSpec；
 * - V6 additive（layout.frames / layout.labels / fabric 源）类型可表达。
 * 编译期闸（tsc）；运行时仅 sanity。
 */
import { describe, expect, it } from 'vitest';
import type {
  DataFabricMapSpecSource,
  MapSpec,
  MapSpecFrame,
} from '@/lib/mapspec-compiler/types';

const legacySpec = {
  version: '1.0',
  view: { center: [116.4, 39.9] as [number, number], zoom: 10 },
  sources: {
    pts: { type: 'geojson' as const, inlineData: { features: [] } },
    tiles: { type: 'vector' as const, tiles: ['https://x/{z}/{x}/{y}.pbf'] },
    img: {
      type: 'raster' as const,
      imageRef: 'ref:raster/1',
      bounds: [116, 39, 117, 40] as [number, number, number, number],
    },
  },
  layers: [
    {
      id: 'pts',
      source: 'pts',
      type: 'circle' as const,
      paint: { 'circle-radius': 5, 'circle-color': '#de2d26' },
      layout: { visibility: 'visible' as const },
      label: { field: 'name', size: 12, color: '#111' },
    },
    {
      id: 'vec',
      source: 'tiles',
      type: 'line' as const,
      sourceLayer: 'data',
      paint: { 'line-width': { method: 'interpolate' as const, field: 'v', stops: [[0, 1]] } },
    },
    { id: 'img', source: 'img', type: 'raster' as const },
  ],
  layout: {
    legend: { visible: true, position: 'top-right' as const },
    controls: [{ type: 'navigation' as const, position: 'top-left' as const }],
    margins: { top: 12, right: 12 },
    components: [
      { id: 't', type: 'title' as const, position: 'top-center' as const },
      {
        id: 'p',
        type: 'legend' as const,
        placement: { mode: 'floating' as const, x: 10, y: 20 },
      },
    ],
  },
  thresholds: { maxFeatures: 2000, timeoutMs: 30000 },
} satisfies MapSpec;

const fabricSource = {
  type: 'data_fabric' as const,
  catalog_item_id: 'c1',
  lazy: true,
  profile: { fields: {} },
} satisfies DataFabricMapSpecSource;

const v6Spec = {
  ...legacySpec,
  version: '1.1',
  sources: { ...legacySpec.sources, fab: fabricSource },
  layers: [...legacySpec.layers, { id: 'fab', source: 'fab', type: 'fill' as const }],
  layout: {
    ...legacySpec.layout,
    labels: { collision: 'deterministic' as const, maxLabels: 400 },
    frames: [
      {
        id: 'f1',
        title: 'Frame One',
        extent: [116, 39, 117, 40] as [number, number, number, number],
        layerOverrides: { pts: { visible: false } },
        pageSize: { width: 297, height: 210 },
        enabled: true,
      },
    ],
  },
} satisfies MapSpec;

describe('MapSpec TS 类型面契约（W3）', () => {
  it('V5 既有形状 satisfies MapSpec（编译兼容面不回退）', () => {
    expect(legacySpec.version).toBe('1.0');
    expect(legacySpec.layers).toHaveLength(3);
  });

  it('V6 additive（fabric/frames/labels）类型可表达', () => {
    const frames: readonly MapSpecFrame[] = v6Spec.layout?.frames ?? [];
    expect(frames[0]?.layerOverrides?.pts?.visible).toBe(false);
    expect(v6Spec.layout?.labels?.collision).toBe('deterministic');
    expect(v6Spec.sources.fab.type).toBe('data_fabric');
  });
});
