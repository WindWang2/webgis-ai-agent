/**
 * Raster Dynamic Stretch —— TS 镜像测试（V11 W3.3，ADR-0163）。
 *
 * 与 tests/cartography/test_raster_stretch_payload.py 消费**同一份**
 * golden fixture：Python 权威实现冻结 payload 与期望 RGBA，本侧
 * applyRasterStretch 须逐字节复现 —— 双路径/双语言 parity。
 */
import { describe, it, expect } from 'vitest';
import fixture from '../../../tests/cartography/golden_corpus/raster_stretch/basic.json';
import {
  applyRasterStretch,
  QUANT_NODATA,
  type RasterStretchPayload,
} from './raster-stretch';

describe('raster dynamic stretch（ADR-0163 双端对拍）', () => {
  it('TS 镜像逐字节复现 expected.rgba', () => {
    const frame = applyRasterStretch(fixture.payload as RasterStretchPayload);
    const expectedBytes = Buffer.from(fixture.expected.rgba, 'base64');
    expect(frame.width).toBe(fixture.expected.width);
    expect(frame.height).toBe(fixture.expected.height);
    expect(Buffer.from(frame.rgba.buffer, frame.rgba.byteOffset, frame.rgba.byteLength))
      .toEqual(expectedBytes);
  });

  it('nodata 格 alpha=0 且量化档为保留值 255', () => {
    const payload = fixture.payload as RasterStretchPayload;
    const frame = applyRasterStretch(payload);
    // fixture 的 (1,3) 格是 NaN → 线性索引 11
    const idx = 11;
    const quant = Buffer.from(payload.values, 'base64');
    expect(quant[idx]).toBe(QUANT_NODATA);
    expect(frame.rgba[idx * 4 + 3]).toBe(0);
    // 有效格不透明
    expect(frame.rgba[0]).toBeGreaterThan(0);
    expect(frame.rgba[3]).toBe(255);
  });

  it('版本守卫：未知版本 fail-closed', () => {
    const bad = { ...(fixture.payload as RasterStretchPayload), version: 99 };
    expect(() => applyRasterStretch(bad)).toThrow(/版本/);
  });

  it('尺寸守卫：量化网格长度不符抛错', () => {
    const bad = {
      ...(fixture.payload as RasterStretchPayload),
      width: 999,
    };
    expect(() => applyRasterStretch(bad)).toThrow(/尺寸不符/);
  });

  it('确定性：同 payload 两次解码逐字节相等', () => {
    const a = applyRasterStretch(fixture.payload as RasterStretchPayload);
    const b = applyRasterStretch(fixture.payload as RasterStretchPayload);
    expect(a.rgba).toEqual(b.rgba);
  });
});
