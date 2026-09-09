/**
 * W5（ADR-0118）：真矢量 SVG 导出单元契约。
 *
 * 锚定四件事：
 * 1. 数据层走既有孪生编译器（mapspec-to-svg，parity 锁定不动）—— 矢量产物
 *    含 <path / <circle 等真矢量元素；
 * 2. 编译产物长文本确定性截断（>60 code points → 前 59 + "…"，与后端
 *    MAX_SVG_LABEL_CHARS 同口径）+ 每处截断一条 label_truncated 诊断；
 * 3. title 等进入 SVG 的文本全部走转义（html.escape quote=True 同链）；
 * 4. 编译/合成异常 → fallbackRaster 位图回退 + vector_svg_fallback_raster
 *    诊断；成功路径发 basemap_omitted_vector_svg（矢量件无栅格底图）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { buildVectorSvgExport } from './vector-svg-export';
import { compileMapSpecToSvg } from '@/lib/mapspec-compiler/mapspec-to-svg';
import type { ExportChromeModel } from './export-chrome';

// 包装真实编译器（parity 锁定 —— 测试只切换 mock 行为，不触实现）。
// vi.mock 工厂被提升到顶层 let 之前 → 经 vi.hoisted 持有原实现引用。
const compilerHolder = vi.hoisted(() => ({
  actual: null as unknown as typeof import('@/lib/mapspec-compiler/mapspec-to-svg')['compileMapSpecToSvg'],
}));
vi.mock('@/lib/mapspec-compiler/mapspec-to-svg', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('@/lib/mapspec-compiler/mapspec-to-svg')>();
  compilerHolder.actual = actual.compileMapSpecToSvg;
  return { ...actual, compileMapSpecToSvg: vi.fn(actual.compileMapSpecToSvg) };
});

const LONG_NAME = '成'.repeat(80);

const SPEC = {
  sources: {
    s1: {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: [
          {
            type: 'Feature',
            geometry: {
              type: 'Polygon',
              coordinates: [[[116.3, 39.8], [116.5, 39.8], [116.5, 40.0], [116.3, 40.0], [116.3, 39.8]]],
            },
            properties: { name: '区域A' },
          },
          {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [116.4, 39.9] },
            properties: { name: LONG_NAME },
          },
        ],
      },
    },
  },
  layers: [
    { id: 'poly', type: 'fill', source: 's1', paint: { 'fill-color': '#2563eb' } },
    { id: 'pts', type: 'circle', source: 's1', paint: { 'circle-color': '#de2d26', 'circle-radius': 6 } },
    { id: 'lbl', type: 'symbol', source: 's1', layout: { 'text-field': '{name}' }, paint: {} },
  ],
};

function chromeWithLegend(): ExportChromeModel {
  return {
    fromSpec: true,
    degradations: [],
    colorbars: [],
    insets: [],
    panels: [],
    legends: [
      {
        kind: 'legend',
        anchor: 'bottom-left',
        legendSpec: {
          type: 'graduated',
          field: '学校数',
          breaks: [0, 10, 20],
          palette: 'Greens',
          palette_colors: ['#edf8e9', '#74c476'],
        },
      },
    ],
  } as unknown as ExportChromeModel;
}

beforeEach(() => {
  vi.mocked(compileMapSpecToSvg).mockImplementation(compilerHolder.actual);
});

describe('buildVectorSvgExport', () => {
  it('produces true vector output (path/circle) plus marginalia (north arrow / frame / legend)', () => {
    const { svg, degradations } = buildVectorSvgExport({
      spec: SPEC,
      viewport: { width: 1200, height: 800 },
      title: '成都学校分布',
      chromeModel: chromeWithLegend(),
      metersPerPixel: 100,
    });

    // 数据层真矢量元素（多边形 → path、点 → circle）
    expect(svg).toContain('<path');
    expect(svg).toContain('<circle');
    // 整饰：图框 / 指北针 / 比例尺 / 图例
    expect(svg).toContain('>N<');
    expect(svg).toContain('学校数');
    expect(svg).toContain('0 – 10');
    // 比例尺标签（确定性 nice 档：100 m/px × 120px → 10 km）
    expect(svg).toContain('10 km');
    expect(degradations).toEqual(
      expect.arrayContaining([expect.objectContaining({ code: 'basemap_omitted_vector_svg' })]),
    );
  });

  it('truncates compiled label text beyond 60 code points (59 + …) with a label_truncated diagnostic', () => {
    const { svg, degradations } = buildVectorSvgExport({
      spec: SPEC,
      viewport: { width: 1200, height: 800 },
    });

    const truncated = '成'.repeat(59) + '…';
    expect(svg).toContain(truncated);
    expect(svg).not.toContain('成'.repeat(60));
    const truncs = degradations.filter((d) => d.code === 'label_truncated');
    expect(truncs).toHaveLength(1);
    expect(truncs[0].detail).toContain('…');
  });

  it('escapes title and subtitle text into the SVG (no raw markup injection)', () => {
    const { svg } = buildVectorSvgExport({
      spec: SPEC,
      viewport: { width: 1200, height: 800 },
      title: '<b>防&注入"</b>',
      subtitle: '<script>alert(1)</script>',
    });

    expect(svg).toContain('&lt;b&gt;防&amp;注入&quot;&lt;/b&gt;');
    expect(svg).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
    expect(svg).not.toContain('<b>');
    expect(svg).not.toContain('<script>');
  });

  it('falls back to the raster wrapper with vector_svg_fallback_raster when the compiler throws', () => {
    vi.mocked(compileMapSpecToSvg).mockImplementation(() => {
      throw new Error('compiler boom');
    });

    const { svg, degradations } = buildVectorSvgExport({
      spec: SPEC,
      viewport: { width: 1200, height: 800 },
      fallbackRaster: () => '<svg>RASTER</svg>',
    });

    expect(svg).toBe('<svg>RASTER</svg>');
    expect(degradations).toEqual([
      expect.objectContaining({ code: 'vector_svg_fallback_raster' }),
    ]);
  });

  it('rethrows when the compiler throws and no raster fallback is available', () => {
    vi.mocked(compileMapSpecToSvg).mockImplementation(() => {
      throw new Error('compiler boom');
    });

    expect(() =>
      buildVectorSvgExport({ spec: SPEC, viewport: { width: 1200, height: 800 } }),
    ).toThrow('compiler boom');
  });
});
