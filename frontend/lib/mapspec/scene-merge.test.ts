/**
 * R1-P1a（评审修复回归）：章节场景旋钮合并 —— 防 terrain.source 缺失
 * 422、防 mode=2d 整块抹掉会话 terrain。纯函数，无 mock。
 */
import { describe, expect, it } from 'vitest';
import { mergeSceneRotary } from './scene-mutation';

describe('mergeSceneRotary', () => {
  it('preserves committed terrain source when overriding exaggeration', () => {
    const merged = mergeSceneRotary(
      { mode: '2.5d', exaggeration: 1.5 },
      { mode: '2d', terrain: { source: 'dem', vertical_unit: 'm', elevation_ref: 'EGM96' } },
      null,
    );
    expect(merged!.mode).toBe('2.5d');
    expect(merged!.terrain).toMatchObject({
      source: 'dem', exaggeration: 1.5, vertical_unit: 'm', elevation_ref: 'EGM96',
    });
  });

  it('mode=2d does not wipe terrain (模式切换不销毁场景配置)', () => {
    const merged = mergeSceneRotary(
      { mode: '2d' },
      { mode: '3d', terrain: { source: 'dem', exaggeration: 1.5 } },
      null,
    );
    expect(merged!.mode).toBe('2d');
    expect(merged!.terrain).toMatchObject({ source: 'dem', exaggeration: 1.5 });
  });

  it('no committed terrain + resolved source id fills source (不 422)', () => {
    const merged = mergeSceneRotary({ exaggeration: 2 }, null, 'dem-src-1');
    expect(merged!.terrain).toMatchObject({ source: 'dem-src-1', exaggeration: 2 });
  });

  it('no terrain anywhere and no exaggeration → no terrain key', () => {
    const merged = mergeSceneRotary({ mode: '3d' }, null, null);
    expect(merged!.terrain).toBeUndefined();
    expect(merged!.mode).toBe('3d');
  });
});
