/**
 * G3D05（ADR-0275）：三维场景导出诚实披露 —— 判定逻辑测试。
 * 与后端 scene_quality 同语义（阈值镜像由
 * tests/cartography/test_scene_export_honesty.py 锁定）。
 */
import { describe, expect, it } from 'vitest';
import {
  SCALE_TRUST_PITCH_MAX,
  effectivePitch,
  sceneExportDisclosure,
  vectorSceneDegradeReceipt,
} from './scene-export-disclosure';

describe('effectivePitch', () => {
  it('prefers finite view pitch over scene recommendation', () => {
    expect(effectivePitch(0, 60)).toBe(0);
    expect(effectivePitch(45, 60)).toBe(45);
  });
  it('falls back to scene pitch when view absent', () => {
    expect(effectivePitch(null, 50)).toBe(50);
    expect(effectivePitch(undefined, 50)).toBe(50);
  });
  it('returns null when both absent or non-finite', () => {
    expect(effectivePitch(null, null)).toBeNull();
    expect(effectivePitch(Number.NaN, Number.NaN)).toBeNull();
  });

  it('non-finite view pitch falls back to scene pitch', () => {
    // NaN 的 view pitch 视同缺席（防上游注入 NaN 直达渲染）。
    expect(effectivePitch(Number.NaN, 30)).toBe(30);
  });
});

describe('sceneExportDisclosure', () => {
  it('flat view with scale bar is reliable (no diagnostic)', () => {
    const r = sceneExportDisclosure({
      viewPitch: SCALE_TRUST_PITCH_MAX,
      hasScaleBar: true,
      hasSceneDisclosure: false,
    });
    expect(r.scaleTrust).toBe('reliable');
    expect(r.scaleDiagnostic).toBeNull();
    expect(r.scaleNoteZh).toBeNull();
  });

  it('perspective + scale bar + no disclosure → warning diagnostic with不可信 copy', () => {
    const r = sceneExportDisclosure({
      viewPitch: 45,
      hasScaleBar: true,
      hasSceneDisclosure: false,
    });
    expect(r.scaleTrust).toBe('perspective');
    expect(r.scaleDiagnostic?.code).toBe('scale_unreliable_perspective');
    expect(r.scaleDiagnostic?.detail).toBe(String(SCALE_TRUST_PITCH_MAX));
    expect(r.scaleNoteZh).toContain('不可信');
    expect(r.scaleNoteZh).toContain('仅供参考');
  });

  it('perspective + disclosure present → softer copy, diagnostic still emitted', () => {
    const r = sceneExportDisclosure({
      viewPitch: 45,
      hasScaleBar: true,
      hasSceneDisclosure: true,
    });
    expect(r.scaleDiagnostic?.code).toBe('scale_unreliable_perspective');
    expect(r.scaleNoteZh).not.toContain('不可信');
    expect(r.scaleNoteZh).toContain('仅供参考');
  });

  it('perspective without scale bar → no diagnostic (无误导面)', () => {
    const r = sceneExportDisclosure({
      viewPitch: 45,
      hasScaleBar: false,
      hasSceneDisclosure: false,
    });
    expect(r.scaleTrust).toBe('perspective');
    expect(r.scaleDiagnostic).toBeNull();
  });

  it('unknown pitch (no camera info) → unknown, silent', () => {
    const r = sceneExportDisclosure({
      viewPitch: null,
      scenePitch: null,
      hasScaleBar: true,
      hasSceneDisclosure: false,
    });
    expect(r.scaleTrust).toBe('unknown');
    expect(r.scaleDiagnostic).toBeNull();
  });
});

describe('vectorSceneDegradeReceipt', () => {
  it('3d/2.5d emit receipt with mode detail', () => {
    expect(vectorSceneDegradeReceipt('3d')).toEqual({
      code: 'svg_3d_perspective_degrade',
      detail: '3d',
    });
    expect(vectorSceneDegradeReceipt('2.5d')?.detail).toBe('2.5d');
  });
  it('2d/absent → null (零噪音)', () => {
    expect(vectorSceneDegradeReceipt('2d')).toBeNull();
    expect(vectorSceneDegradeReceipt(undefined)).toBeNull();
    expect(vectorSceneDegradeReceipt(null)).toBeNull();
  });
});
