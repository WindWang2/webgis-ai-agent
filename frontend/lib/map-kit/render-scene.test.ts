/**
 * V5（ADR-0118 W10）：render scene 语义 golden corpus + live 合成语义响应。
 *
 * - describeRenderScene 是 live ↔ export 语义 parity 的共同 oracle；
 * - corpus 用例（本文件内联定义，投影口径变更时同步更新 golden）锁定
 *   语义快照的形状（在场/可见/披露），不做像素断言；
 * - live 合成（composeLiveMapSpec + pendingPresentation）必须在语义快照
 *   中真实反映可见性/删除 —— 这是 W7 导出真相同源契约的场景级锚点。
 */
import { describe, expect, it } from 'vitest';
import { describeRenderScene, serializeRenderScene, type RenderSceneSnapshot } from './render-scene';
import { composeLiveMapSpec } from '@/lib/mapspec/live-spec';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

const CORPUS_FULL: MapSpec = {
  version: 1,
  view: { center: [116.4, 39.9], zoom: 10 },
  sources: { s1: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } } },
  layers: [
    {
      id: 'choropleth',
      type: 'fill',
      source: 's1',
      paint: { 'fill-color': '#08519c' },
      legend_spec: {
        type: 'graduated',
        field: '人口',
        title: '区县人口',
        breaks: [0, 10, 20, 30],
        palette_colors: ['#eff3ff', '#6baed6', '#08519c'],
        nodata: { color: '#cccccc', label: '无数据' },
      },
      label: { field: 'name', size: 12, color: '#111827' },
    },
    { id: 'basemap-ref', type: 'raster', source: 's1', paint: {} },
  ],
  layout: {
    components: [
      { id: 'title-1', type: 'title', enabled: true, position: 'top-left', options: { text: '北京市人口专题图' } },
      { id: 'legend-1', type: 'legend', enabled: true, position: 'bottom-left' },
      { id: 'north-1', type: 'north_arrow', enabled: false, position: 'top-right' },
    ],
  },
} as unknown as MapSpec;

const GOLDEN_FULL: RenderSceneSnapshot = {
  layers: [
    { id: 'basemap-ref', visible: true, hasLegendSpec: false, hasLabel: false },
    { id: 'choropleth', visible: true, hasLegendSpec: true, hasLabel: true },
  ],
  components: [
    { id: 'legend-1', type: 'legend', position: 'bottom-left', variant: '', enabled: true, collapsed: false },
    { id: 'north-1', type: 'north_arrow', position: 'top-right', variant: '', enabled: false, collapsed: false },
    { id: 'title-1', type: 'title', position: 'top-left', variant: '', enabled: true, collapsed: false },
  ],
  legends: [
    { layerId: 'choropleth', title: '区县人口', entryCount: 4, hasNodata: true },
  ],
  degradationCodes: [],
};

describe('describeRenderScene · golden corpus（ADR-0118 W10）', () => {
  it('full chrome spec 投影与 golden 逐字一致', () => {
    const snap = describeRenderScene(CORPUS_FULL);
    expect(snap).toEqual(GOLDEN_FULL);
    // 序列化口径稳定（golden 落盘/复算一致）
    expect(serializeRenderScene(snap)).toBe(serializeRenderScene(GOLDEN_FULL));
  });

  it('空 spec 投影为空场景（无隐式组件/图例）', () => {
    const snap = describeRenderScene(null);
    expect(snap).toEqual({ layers: [], components: [], legends: [], degradationCodes: [] });
  });

  it('降级码去重排序进入快照', () => {
    const snap = describeRenderScene(CORPUS_FULL, {
      degradations: [
        { code: 'legend_entries_truncated' },
        { code: 'basemap_omitted_vector_svg' },
        { code: 'legend_entries_truncated' },
      ],
    });
    expect(snap.degradationCodes).toEqual(['basemap_omitted_vector_svg', 'legend_entries_truncated']);
  });
});

describe('describeRenderScene · live 合成语义响应（W7 场景级锚点）', () => {
  it('pendingPresentation 的可见性翻转真实反映在语义快照', () => {
    const hud = {
      layers: [],
      processLayers: [],
    } as unknown as Parameters<typeof composeLiveMapSpec>[1];
    const live = composeLiveMapSpec(CORPUS_FULL, hud, {
      'choropleth': { visible: false },
    } as unknown as Parameters<typeof composeLiveMapSpec>[2]);
    const snap = describeRenderScene(live);
    const choropleth = snap.layers.find((l) => l.id === 'choropleth');
    expect(choropleth?.visible).toBe(false);
    // 语义披露：图例通道/label 通道不因可见性翻转而丢失（隐藏 ≠ 删除语义）
    expect(choropleth?.hasLegendSpec).toBe(true);
    expect(choropleth?.hasLabel).toBe(true);
  });
});
