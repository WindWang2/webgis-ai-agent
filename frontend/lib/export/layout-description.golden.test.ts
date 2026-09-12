/**
 * ac-08（ADR-0157 P6）· 版面描述 IR golden 对拍 —— TS 侧（参照实现面）。
 *
 * 与 tests/cartography/test_layout_description_golden.py 消费**同一份**
 * fixture：expected 由本侧（TS 参照实现）生成冻结，Python 镜像须逐字段
 * 复现（浮点 1e-9）。本文件同时反向锁定：参照实现漂移即红灯。
 */
import { describe, it, expect } from 'vitest';
import fixture from '../../../tests/cartography/golden_corpus/layout_description/basic_cmyk_overflow.json';
import { buildPublicationLayout, PUBLICATION_LAYOUT_VERSION } from './layout-description';
import { exportBoundsForFrame, mercNormY } from './extent';

const input = fixture.input as {
  paperSize: 'A4';
  orientation: 'landscape';
  dpi: number;
  frame: { width: number; height: number };
  requestTitle: string;
  metersPerPixel: number;
  extent: { mask: [number, number, number, number]; export: [number, number, number, number]; dataOverflow: boolean };
  colorMode: 'cmyk';
};

describe('layout description golden（ADR-0157 P6 双端对拍）', () => {
  it('TS 参照实现复现 expected（全字段深等；chromeModel 为 canvas 运行时面不入 JSON 契约）', () => {
    const ir = buildPublicationLayout({
      paperSize: input.paperSize,
      orientation: input.orientation,
      dpi: input.dpi,
      frame: input.frame,
      chromeModel: null,
      requestTitle: input.requestTitle,
      metersPerPixel: input.metersPerPixel,
      extent: input.extent,
      colorMode: input.colorMode,
    });
    expect(ir.version).toBe(PUBLICATION_LAYOUT_VERSION);
    // chromeModel = TS 侧 canvas 绘制面（export-chrome 2657 行绘制器的消费
    // 面），非跨语言契约字段（Python 镜像无对应物）→ JSON 对拍显式排除。
    const { chromeModel: _canvasOnly, ...jsonIr } = ir as typeof ir & {
      chromeModel?: unknown;
    };
    void _canvasOnly;
    expect(jsonIr).toEqual(fixture.expected);
  });

  it('extent 数学复现冻结边界（Mercator 归一量纲）', () => {
    const m = fixture.extentMath as {
      mask: [number, number, number, number];
      aspectWH: number;
      expectedExport: [number, number, number, number];
      mercNormYProbes: Array<{ lat: number; y: number }>;
    };
    const out = exportBoundsForFrame(m.mask, m.aspectWH)!;
    out.forEach((v, i) => expect(v).toBeCloseTo(m.expectedExport[i], 9));
    for (const p of m.mercNormYProbes) {
      expect(mercNormY(p.lat)).toBeCloseTo(p.y, 12);
    }
  });
});
