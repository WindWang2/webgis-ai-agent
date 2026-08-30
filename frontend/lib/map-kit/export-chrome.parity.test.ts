/**
 * §13 Live/Export Parity 矩阵 —— 目标要求的十维 parity 契约位：
 *
 *   component presence / placement / variant / binding / collapsed state /
 *   legend content / colorbar range / annotation text / inset extent /
 *   chart data
 *
 * 语义锚点：live（resolveMapComponents，MapSpecChrome 挂载语义）与
 * export（buildExportChrome，PNG/PDF/SVG 共用画布 chrome 链 —— SVG 为
 * 位图包装、PDF 为画布嵌入，因此画布 chrome 即三格式语义源）。
 * 一份综合 MapSpec 驱动，逐维断言两侧一致（确定性、无 map 实例）。
 */
import { describe, expect, it } from 'vitest';
import { resolveMapComponents } from '@/lib/map-components/resolve-components';
import {
  buildExportChrome,
  type BuildExportChromeOptions,
} from '@/lib/map-kit/export-chrome';

const LEGEND_SPECS = {
  'heat-1': {
    type: 'continuous',
    field: 'kernel 密度',
    min: 0.2,
    max: 48.5,
    palette_colors: ['#1e3a8a', '#ef4444'],
    unit: '人/km²',
  },
  'district-1': {
    type: 'graduated',
    field: '学校数',
    breaks: [0, 10, 20, 50],
    palette_colors: ['#edf8e9', '#bae4b3', '#74c476'],
  },
} as unknown as BuildExportChromeOptions['legendSpecsByLayer'];

const INLINE_CHART = {
  type: 'bar',
  title: '各区学校数量',
  data: [{ name: '锦江', value: 12 }, { name: '青羊', value: 9 }],
};

const COMPREHENSIVE_SPEC = {
  layout: {
    components: [
      { id: 'title', type: 'title', enabled: true, position: 'top-center',
        options: { text: '成都学校分布' } },
      { id: 'subtitle', type: 'subtitle', enabled: true, position: 'top-center',
        options: { text: '2025 年秋季学期' } },
      { id: 'cb-main', type: 'continuous_colorbar', enabled: true,
        position: 'bottom-right', options: { layerId: 'heat-1' } },
      { id: 'lg-dist', type: 'legend', enabled: true,
        position: 'bottom-left', options: { layerId: 'district-1' } },
      { id: 'border', type: 'map_border', enabled: true, variant: 'academic' },
      { id: 'inset-1', type: 'inset_map', enabled: true, position: 'top-right',
        options: { bbox: [97, 26, 108, 34], mainBbox: [103.9, 30.6, 104.2, 30.8],
                   label: '中国范围' } },
      { id: 'note-text', type: 'annotation', enabled: true, position: 'top-left',
        options: { variant: 'text', text: '数据来源：成都市教育局' } },
      { id: 'note-callout', type: 'annotation', enabled: true,
        options: { variant: 'callout', text: '汶川震中 M8.0', anchor: [103.4, 31.0] } },
      { id: 'chart-a', type: 'chart_panel', enabled: true,
        options: { chart: INLINE_CHART } },
      { id: 'chart-b', type: 'chart_panel', enabled: true,
        options: { chartRef: 'ref:chart-type-mix' },
        placement: { mode: 'floating', x: 400, y: 40, collapsed: true } },
      { id: 'stats', type: 'statistics_panel', enabled: true,
        options: { stats: { title: '统计', items: [{ label: '总数', value: 128 }] } },
        placement: { mode: 'floating', x: 20, y: 300, collapsed: true } },
    ],
  },
} as const;

function buildOpts(): BuildExportChromeOptions {
  return {
    spec: COMPREHENSIVE_SPEC as unknown as BuildExportChromeOptions['spec'],
    viewport: { width: 1200, height: 800 },
    legendSpecsByLayer: LEGEND_SPECS,
    loadChart: async (ref) =>
      ref === 'ref:chart-type-mix'
        ? ({ type: 'pie', title: '学校类别构成', data: [{ name: '小学', value: 64 }] } as never)
        : null,
  };
}

const CANVAS = { width: 1200, height: 800 };

describe('§13 live/export semantic parity（十维矩阵）', () => {
  it('① component presence：每个 enabled 可视组件都进入导出模型', async () => {
    const live = resolveMapComponents(COMPREHENSIVE_SPEC as never).filter((c) => c.enabled);
    const model = await buildExportChrome(buildOpts(), CANVAS);
    const exportIds = new Set<string>();
    if (model.title) exportIds.add(model.title.kind === 'title' ? 'title' : model.title.kind);
    if (model.subtitle) exportIds.add('subtitle');
    if (model.northArrow) exportIds.add('north_arrow');
    if (model.scaleBar) exportIds.add('scale_bar');
    if (model.attribution) exportIds.add('attribution');
    if (model.border) exportIds.add('map_border');
    for (const _el of model.legends) exportIds.add('legend');
    for (const _el of model.colorbars) exportIds.add('continuous_colorbar');
    for (const _el of model.insets) exportIds.add('inset_map');
    for (const el of model.panels) {
      if (el.kind === 'statistics') exportIds.add('statistics_panel');
      if (el.kind === 'chart') exportIds.add('chart_panel');
      if (el.kind === 'annotation') exportIds.add('annotation');
    }
    for (const c of live) {
      expect(exportIds.has(c.type), `export missing live component ${c.id} (${c.type})`).toBe(true);
    }
  });

  it('② placement：anchored 导出槽位 === live anchor；floating 盒 1:1 保持', async () => {
    const model = await buildExportChrome(buildOpts(), CANVAS);
    const liveById = new Map(
      resolveMapComponents(COMPREHENSIVE_SPEC as never)
        .filter((c) => c.enabled)
        .map((c) => [c.id, c]),
    );
    // anchored：inset top-right
    expect(model.insets[0].anchor).toBe(liveById.get('inset-1')!.anchor);
    // floating 1:1（viewport == canvas，无缩放；未越界 → 不夹取）
    const chartB = model.panels.find((p) => p.kind === 'chart' && p.rect);
    expect(chartB!.rect).toEqual({ x: 400, y: 40 });
    const stats = model.panels.find((p) => p.kind === 'statistics');
    expect(stats!.rect).toEqual({ x: 20, y: 300 });
  });

  it('③ variant：map_border academic 变体两侧一致', async () => {
    const model = await buildExportChrome(buildOpts(), CANVAS);
    expect(model.border!.variant).toBe('academic');
    const live = resolveMapComponents(COMPREHENSIVE_SPEC as never)
      .find((c) => c.id === 'border');
    expect(live!.variant).toBe('academic');
  });

  it('④ binding：图例/色条绑定各自图层的 legend_spec（引用一致）', async () => {
    const model = await buildExportChrome(buildOpts(), CANVAS);
    expect(model.colorbars).toHaveLength(1);
    expect(model.colorbars[0].legendSpec).toBe(LEGEND_SPECS['heat-1']);
    expect(model.legends).toHaveLength(1);
    expect(model.legends[0].legendSpec).toBe(LEGEND_SPECS['district-1']);
    const liveCb = resolveMapComponents(COMPREHENSIVE_SPEC as never)
      .find((c) => c.id === 'cb-main');
    expect(liveCb!.layerId).toBe('heat-1');
  });

  it('⑤ collapsed state：折叠的 chart/statistics 导出折叠标题条（不展开）', async () => {
    const model = await buildExportChrome(buildOpts(), CANVAS);
    const chartB = model.panels.find((p) => p.kind === 'chart' && p.rect);
    expect(chartB!.text).toBe('学校类别构成'); // collapsed → text 携带 ref 图表标题
    const chartA = model.panels.find((p) => p.kind === 'chart' && !p.rect);
    expect(chartA!.text).toBeUndefined(); // 未折叠 → 正常绘制
    const stats = model.panels.find((p) => p.kind === 'statistics');
    expect(stats!.text).toBe('统计');
  });

  it('⑥⑦ legend content / colorbar range：内容与量化范围同源', async () => {
    const model = await buildExportChrome(buildOpts(), CANVAS);
    const cbSpec = model.colorbars[0].legendSpec as { min: number; max: number; unit: string };
    expect(cbSpec.min).toBe(0.2);
    expect(cbSpec.max).toBe(48.5);
    expect(cbSpec.unit).toBe('人/km²');
    const lgSpec = model.legends[0].legendSpec as { breaks: number[] };
    expect(lgSpec.breaks).toEqual([0, 10, 20, 50]);
  });

  it('⑧⑨⑩ annotation text / inset extent / chart data 同链', async () => {
    const model = await buildExportChrome(buildOpts(), CANVAS);
    const annotations = model.panels.filter((p) => p.kind === 'annotation');
    const callout = annotations.find((p) => p.anchorCoordinate);
    expect(callout!.text).toBe('汶川震中 M8.0');
    expect(callout!.anchorCoordinate).toEqual([103.4, 31.0]);
    expect(annotations.some((p) => p.text === '数据来源：成都市教育局')).toBe(true);

    expect(model.insets[0].insetBbox).toEqual({ west: 97, south: 26, east: 108, north: 34 });
    expect(model.insets[0].insetMainBbox).toEqual({ west: 103.9, south: 30.6, east: 104.2, north: 30.8 });
    expect(model.insets[0].text).toBe('中国范围');

    const charts = model.panels.filter((p) => p.kind === 'chart');
    const inline = charts.find((p) => !p.rect);
    expect(inline!.chart!.data).toEqual(INLINE_CHART.data);
    const fromRef = charts.find((p) => p.rect);
    expect(fromRef!.chart!.type).toBe('pie'); // ref:chart-type-mix 经 loadChart 拉取
  });

  it('禁用组件不进导出模型（enabled parity）', async () => {
    const spec = {
      layout: { components: [
        { id: 'title', type: 'title', enabled: true, options: { text: 'T' } },
        { id: 'cb', type: 'continuous_colorbar', enabled: false, options: { layerId: 'heat-1' } },
      ] },
    };
    const model = await buildExportChrome(
      { ...(buildOpts()), spec: spec as never },
      CANVAS,
    );
    expect(model.colorbars).toHaveLength(0);
    expect(model.colorbar).toBeUndefined();
  });
});
