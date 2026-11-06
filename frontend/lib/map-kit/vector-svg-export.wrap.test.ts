/**
 * ADR-0283 D2 · 关键文本 wrap-not-clip —— SVG 矢量链绘制面。
 *
 * wrapped 条目（layout.texts[].lines）按行输出多个 <text>；行距确定性
 * （title 22px×1.3=29、subtitle 12px×1.3=16）；单行形态与旧输出一致。
 */
import { describe, it, expect } from 'vitest';
import { buildVectorSvgExport } from './vector-svg-export';
import { buildPublicationLayout } from '../export/layout-description';

const LONG_TITLE =
  '二〇二六年秋季华北平原城市群基础教育资源空间分异研究：基于多源地理开放数据的区县级统计与择校可达性综合评估报告'
  + '（覆盖京津冀鲁豫五省市一百零二个区县、四千三百余所学校的分布与师资可达性联合分析）';

const SPEC = {
  sources: {
    s1: {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: [
          {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [116.4, 39.9] },
            properties: { name: '学校A' },
          },
        ],
      },
    },
  },
  layers: [
    { id: 'pts', type: 'circle', source: 's1', paint: { 'circle-color': '#de2d26', 'circle-radius': 6 } },
  ],
};

function layoutWithTitle(title: string) {
  return buildPublicationLayout({
    paperSize: 'screen',
    orientation: 'landscape',
    dpi: 96,
    frame: { width: 1200, height: 800 },
    chromeModel: null,
    requestTitle: title,
  });
}

describe('vector-svg-export wrap-not-clip（ADR-0283 D2）', () => {
  it('wrapped 标题逐行输出 <text>，两行 y 间距 29（22px×1.3）', () => {
    const { svg, degradations } = buildVectorSvgExport({
      spec: SPEC as never,
      layout: layoutWithTitle(LONG_TITLE),
    });
    const titleTexts = [
      ...svg.matchAll(
        /<text [^>]*y="(\d+)"[^>]*font-size="22"[^>]*>([^<]*)<\/text>/g,
      ),
    ];
    expect(titleTexts.length).toBeGreaterThan(2);
    // Reflow is constrained by actual frame width, beyond the semantic IR cap.
    expect(titleTexts.every(match => Array.from(match[2]).length * 22 < 1200)).toBe(true);
    expect(Number(titleTexts[1][1]) - Number(titleTexts[0][1])).toBe(29);
    // 内容完整：两行拼接 = 原标题（无字符丢失）
    const joined = titleTexts.map((m) => m[2]).join('');
    expect(joined.replace(/\s/g, '')).toBe(LONG_TITLE.replace(/\s/g, ''));
    // 折行披露（非截断）
    expect(degradations.some((d) => d.code === 'key_text_wrapped')).toBe(true);
    expect(degradations.some((d) => d.code === 'title_truncated')).toBe(false);
  });

  it('短标题单行输出（旧形态），无折行披露', () => {
    const { svg, degradations } = buildVectorSvgExport({
      spec: SPEC as never,
      layout: layoutWithTitle('成都学校分布'),
    });
    const titleTexts = [
      ...svg.matchAll(/<text [^>]*font-size="22"[^>]*>([^<]*)<\/text>/g),
    ];
    expect(titleTexts.length).toBe(1);
    expect(titleTexts[0][1]).toBe('成都学校分布');
    expect(degradations.some((d) => d.code === 'key_text_wrapped')).toBe(false);
  });
});
