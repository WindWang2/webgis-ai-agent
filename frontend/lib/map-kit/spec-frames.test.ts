/**
 * V6（ADR-0120 W9）：spec 级 frames → ExportFrame 适配层测试。
 * view→extent 换算与后端 publication _frame_geometry 同口径。
 */
import { describe, expect, it } from 'vitest';
import { specFramesToExportFrames } from './spec-frames';

describe('spec-frames 适配（W9）', () => {
  it('extent / title / enabled 过滤', () => {
    const r = specFramesToExportFrames([
      { id: 'f1', title: '第一页', extent: [115, 38, 118, 41] },
      { id: 'off', extent: [0, 0, 1, 1], enabled: false },
      { id: 'f2', extent: [110, 30, 122, 42] },
    ]);
    expect(r.frames).toHaveLength(2);
    expect(r.frames[0]).toEqual({ extent: [115, 38, 118, 41], title: '第一页' });
    expect(r.frames[1].title).toBe('f2'); // 无 title 回退 id
    expect(r.disabledCount).toBe(1);
  });

  it('view（center/zoom）→ extent：zoom 10 → 经度跨 0.3516°（与后端同式）', () => {
    const r = specFramesToExportFrames([
      { id: 'v1', view: { center: [116, 39], zoom: 10 } },
    ]);
    expect(r.frames).toHaveLength(1);
    const [w, s, e, n] = r.frames[0].extent!;
    expect(w).toBeCloseTo(116 - 360 / 1024 / 2, 6);
    expect(e).toBeCloseTo(116 + 360 / 1024 / 2, 6);
    expect(s).toBeCloseTo(39 - 360 / 1024 / 4, 6);
    expect(n).toBeCloseTo(39 + 360 / 1024 / 4, 6);
  });

  it('无 extent 且无 center view → unmappable 计数（不伪造）', () => {
    const r = specFramesToExportFrames([{ id: 'bad', view: { zoom: 10 } }]);
    expect(r.frames).toHaveLength(0);
    expect(r.unmappableCount).toBe(1);
  });

  it('50 帧上限截断（与 MAX_FRAMES 同口径）', () => {
    const frames = Array.from({ length: 60 }, (_, i) => ({
      id: `f${i}`,
      extent: [0, 0, 1, 1],
    }));
    const r = specFramesToExportFrames(frames);
    expect(r.frames).toHaveLength(50);
  });

  it('undefined / 非数组安全', () => {
    expect(specFramesToExportFrames(undefined).frames).toEqual([]);
    expect(specFramesToExportFrames(null).frames).toEqual([]);
  });
});
