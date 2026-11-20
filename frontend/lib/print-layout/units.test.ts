import { describe, expect, it } from 'vitest';
import corpus from '../../../tests/cartography/golden_corpus/print_layout_units/basic.json';
import {
  canvasUnitsToMm, mmToCanvasUnits, mmToSamplePx, ptToCanvasUnits,
  previewScale, unitsCorpus,
} from './units';

describe('G34 print layout unit corpus (Python/TS parity)', () => {
  it('matches the Python-generated golden corpus field by field', () => {
    expect(unitsCorpus()).toEqual(corpus);
  });

  it('keeps physical millimetres constant across 96/150/300 DPI', () => {
    const mm = 15;
    const roundTrip = [96, 150, 300].map(
      (dpi) => canvasUnitsToMm(mmToCanvasUnits(mm, dpi), dpi));
    roundTrip.forEach((v) => expect(v).toBeCloseTo(mm, 9));
  });

  it('keeps physical point size constant while user units scale with DPI', () => {
    const u96 = ptToCanvasUnits(12, 96);
    const u150 = ptToCanvasUnits(12, 150);
    const u300 = ptToCanvasUnits(12, 300);
    expect(u150 / u96).toBeCloseTo(150 / 96, 9);
    expect(u300 / u96).toBeCloseTo(300 / 96, 9);
  });

  it('changes sampled pixels with DPI but not the millimetre geometry', () => {
    expect(mmToSamplePx(210, 300)).toBeGreaterThan(mmToSamplePx(210, 96));
    expect(mmToSamplePx(210, 300) / mmToSamplePx(210, 150)).toBeCloseTo(2, 9);
    // 缩放预览只是显示比例，不改变持久毫米坐标来源
    expect(mmToCanvasUnits(15, 300) * previewScale(2)).toBeCloseTo(
      2 * mmToCanvasUnits(15, 300), 9);
  });
});
