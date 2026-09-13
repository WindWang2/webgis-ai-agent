/**
 * ac-08（ADR-0157 P2/P7）· SVG-PNG 同源渲染差异探针（结构面）。
 *
 * recon §3 双链差异清单的漂移逐项封口 —— canvas 链与 SVG 链从同一
 * buildPublicationLayout 记录消费决策；本文件断言 SVG 消费端不再有
 * 私有决策（此前：图例只取 legends[0]、署名缺席、标题不读 spec 回退链）。
 * 像素面由 scripts/ac08/dpi-line-probe.mjs（DPI 对比）与既有字节 parity
 * 金样（mapspec-to-svg）覆盖。
 */
import { describe, expect, it } from 'vitest';
import { buildVectorSvgExport } from './vector-svg-export';
import { buildPublicationLayout } from '../export/layout-description';
import type { ExportChromeModel } from './export-chrome';

const SPEC = {
  sources: {
    s1: {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: [
          {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [104.06, 30.67] },
            properties: { name: '锦江区' },
          },
        ],
      },
    },
  },
  layers: [
    { id: 'l1', type: 'circle', source: 's1', paint: { 'circle-color': '#de2d26' } },
  ],
};

function chromeWithTwoLegends(): ExportChromeModel {
  const legend = (field: string, colors: string[]) =>
    ({
      kind: 'legend',
      anchor: 'bottom-left',
      legendSpec: {
        type: 'graduated',
        field,
        breaks: [0, 10],
        palette: 'Blues',
        palette_colors: colors,
      },
    }) as unknown as ExportChromeModel['legends'][number];
  return {
    fromSpec: true,
    degradations: [],
    colorbars: [],
    insets: [],
    panels: [],
    legends: [
      legend('学校数', ['#eff6ff', '#1d4ed8']),
      legend('人口数', ['#fef2f2', '#b91c1c']),
    ],
    attribution: { kind: 'attribution', anchor: 'bottom-left', text: '数据源：成都开放数据' },
  } as unknown as ExportChromeModel;
}

describe('SVG 消费端与 canvas 同源（版面描述 IR 单一决策记录）', () => {
  it('标题回退链：请求缺席 → spec 组件文本进 SVG（旧实现不读 spec 链）', () => {
    const layout = buildPublicationLayout({
      paperSize: 'screen',
      orientation: 'landscape',
      dpi: 96,
      frame: { width: 1200, height: 800 },
      chromeModel: null,
      specTitle: '规范标题：人口密度',
    });
    const { svg } = buildVectorSvgExport({ spec: SPEC, layout });
    expect(svg).toContain('规范标题：人口密度');
  });

  it('图例多实例：legends 两个实例的字段全部入 SVG（旧实现只取 legends[0]）', () => {
    const chrome = chromeWithTwoLegends();
    const layout = buildPublicationLayout({
      paperSize: 'screen',
      orientation: 'landscape',
      dpi: 96,
      frame: { width: 1200, height: 800 },
      chromeModel: chrome,
    });
    const { svg } = buildVectorSvgExport({ spec: SPEC, layout });
    expect(svg).toContain('学校数');
    expect(svg).toContain('人口数');
  });

  it('署名：chrome attribution 文本进 SVG（旧实现缺席）', () => {
    const chrome = chromeWithTwoLegends();
    const layout = buildPublicationLayout({
      paperSize: 'screen',
      orientation: 'landscape',
      dpi: 96,
      frame: { width: 1200, height: 800 },
      chromeModel: chrome,
      attributionText: '数据源：成都开放数据',
    });
    const { svg } = buildVectorSvgExport({ spec: SPEC, layout });
    expect(svg).toContain('数据源：成都开放数据');
  });

  it('比例尺数字与画布同记录：layout.scaleBar.nice 直接驱动 SVG 条长', () => {
    const layout = buildPublicationLayout({
      paperSize: 'screen',
      orientation: 'landscape',
      dpi: 96,
      frame: { width: 1200, height: 800 },
      chromeModel: null,
      metersPerPixel: 100,
    });
    const { svg } = buildVectorSvgExport({ spec: SPEC, layout });
    // IR 单源 label（computeNiceScale(100, 120) → 10 km）
    expect(svg).toContain(layout.scaleBar!.label);
    expect(svg).toContain('10 km');
  });

  it('出版档：cmyk → 裁切标记进入 SVG（出血外缘几何）', () => {
    const layout = buildPublicationLayout({
      paperSize: 'A4',
      orientation: 'landscape',
      dpi: 300,
      frame: { width: 2480, height: 1754 },
      chromeModel: null,
      colorMode: 'cmyk',
    });
    const { svg } = buildVectorSvgExport({ spec: SPEC, layout });
    expect(svg).toContain('stroke-width="0.75"'); // renderSvgCropMarks 臂线
  });

  it('mixed 判定（review-r1 修正）：heatmap 近似层 → mixed；纯矢量长标签 → 仍 vector', () => {
    const base = {
      paperSize: 'screen' as const,
      orientation: 'landscape' as const,
      dpi: 96,
      frame: { width: 1200, height: 800 },
      chromeModel: null,
    };
    // heatmap 层 → 编译器 layer_approximated_heatmap 诊断 → mixed
    const heatmapSpec = {
      sources: {
        s1: {
          type: 'geojson',
          data: {
            type: 'FeatureCollection',
            features: [
              { type: 'Feature', geometry: { type: 'Point', coordinates: [104.06, 30.67] }, properties: { w: 3 } },
            ],
          },
        },
      },
      layers: [{ id: 'h1', type: 'heatmap', source: 's1', paint: { 'heatmap-radius': 15 } }],
    };
    const mixed = buildVectorSvgExport({ spec: heatmapSpec, layout: buildPublicationLayout(base) });
    expect(mixed.svg).toContain('data-export-content="mixed"');

    // 纯矢量（circle）+ 长标签截断：label 截断不属于层近似 → 仍 vector
    const longLabelSpec = {
      sources: {
        s1: {
          type: 'geojson',
          data: {
            type: 'FeatureCollection',
            features: [
              {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [104.06, 30.67] },
                properties: { name: '成'.repeat(80) },
              },
            ],
          },
        },
      },
      layers: [
        { id: 'p1', type: 'circle', source: 's1', paint: { 'circle-color': '#de2d26' } },
        { id: 'lbl', type: 'symbol', source: 's1', layout: { 'text-field': '{name}' }, paint: {} },
      ],
    };
    const pure = buildVectorSvgExport({ spec: longLabelSpec, layout: buildPublicationLayout(base) });
    expect(pure.svg).toContain('data-export-content="vector"');
    expect(pure.degradations.some((d) => d.code === 'label_truncated')).toBe(true);
  });
});
