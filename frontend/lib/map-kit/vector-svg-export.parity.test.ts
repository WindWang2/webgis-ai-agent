/**
 * V5（ADR-0118 W10）跨孪生长文本截断 parity —— TS 侧。
 *
 * 与 tests/unit/test_compiler_parity_long_label.py 共享同一 fixture
 * （220 字符 CJK 标注）。python 孪生在渲染器内截断；TS 用户导出路径在
 * buildVectorSvgExport 后处理截断。两侧对同一 fixture 必须产出同一条
 * 截断文本（前 59 code points + "…"），且全文本不得进入产物。
 */
import { describe, expect, it } from 'vitest';
import fixture from '../../../tests/fixtures/compiler_parity_long_label.json';
import { buildVectorSvgExport } from './vector-svg-export';

const LONG_LABEL: string = fixture.sources.s1.data.features[0].properties.name;

describe('vector SVG export · cross-twin long-label truncation parity', () => {
  it('fixture label is long enough to force truncation', () => {
    expect(Array.from(LONG_LABEL).length).toBeGreaterThanOrEqual(200);
  });

  it('emits the identical 59-cp prefix + ellipsis as the Python twin', () => {
    const result = buildVectorSvgExport({
      spec: fixture,
      viewport: { width: 800, height: 600 },
    });
    const expected = Array.from(LONG_LABEL).slice(0, 59).join('') + '…';
    expect(result.svg).toContain(expected);
    expect(result.svg).not.toContain(LONG_LABEL);
  });

  it('emits a label_truncated degradation', () => {
    const result = buildVectorSvgExport({
      spec: fixture,
      viewport: { width: 800, height: 600 },
    });
    expect(result.degradations.some((d) => d.code === 'label_truncated')).toBe(true);
  });
});
