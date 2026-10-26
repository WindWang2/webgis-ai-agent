/**
 * G3D05（ADR-0275）scene_disclosure 渲染器回归：注册在场 + spec.scene
 * 单一真相派生（mode/夸张/垂直基准）+ 比例尺可信度徽标 pitch 三态 +
 * 无 scene 块面板缺席 + extraNotes 附注。harness 复用 disclosure-panels
 * .test.tsx（renderComponent + ctx.spec 携带 committed MapSpec）。
 */
import { describe, expect, it, beforeEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import React from 'react';
import { renderComponent } from './index';
import { getComponentRenderer } from './registry';
import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';

function makeSpec(
  scene?: Record<string, unknown>,
  view?: Record<string, unknown>,
): MapSpec {
  return {
    version: '1.0',
    sources: {},
    layers: [],
    ...(scene ? { scene } : {}),
    ...(view ? { view } : {}),
  } as unknown as MapSpec;
}

function ctxFor(spec: MapSpec | null) {
  return { spec, zoom: 10, centerLat: 30, bearing: 0 };
}

function comp(options: Record<string, unknown> = {}): MapSpecComponent {
  return {
    id: 'scene-disclosure-1',
    type: 'scene_disclosure',
    enabled: true,
    position: 'bottom-right',
    priority: 45,
    options,
  } as never;
}

function renderDisclosure(spec: MapSpec | null, options: Record<string, unknown> = {}) {
  const el = renderComponent(comp(options), ctxFor(spec) as never);
  return render(<>{el}</>);
}

describe('scene_disclosure renderer', () => {
  beforeEach(() => {
    cleanup();
  });

  it('在 registry 注册（side-effect import）', () => {
    expect(getComponentRenderer('scene_disclosure')).toBeDefined();
  });

  it('渲染场景模式/地形夸张/垂直单位/高程基准行（spec.scene 单一真相）', () => {
    const { container } = renderDisclosure(makeSpec(
      {
        mode: '3d',
        terrain: { source: 'dem-1', exaggeration: 1.5, vertical_unit: 'm', elevation_ref: 'EGM96' },
        camera: { pitch: 5 },
      },
      { pitch: 5 },
    ));
    const panel = container.querySelector('[data-testid="spec-chrome-scene-disclosure"]');
    expect(panel).toBeTruthy();
    const text = container.textContent ?? '';
    expect(text).toContain('场景模式：3D');
    expect(text).toContain('地形夸张系数：1.5×');
    expect(text).toContain('垂直单位：m');
    expect(text).toContain('高程基准：EGM96');
    // aria 契约（目录 accessibility：role=note + 场景披露）
    const note = container.querySelector('[role="note"]');
    expect(note?.getAttribute('aria-label')).toBe('场景披露');
  });

  it('exaggeration > 1 → 追加（垂直比例夸张，非真实坡度）警示后缀', () => {
    const { container } = renderDisclosure(makeSpec(
      { mode: '3d', terrain: { source: 'dem-1', exaggeration: 2, elevation_ref: 'EGM96' } },
      { pitch: 0 },
    ));
    expect(container.textContent).toContain('地形夸张系数：2×（垂直比例夸张，非真实坡度）');
  });

  it('exaggeration ≤ 1 → 无警示后缀', () => {
    const { container } = renderDisclosure(makeSpec(
      { mode: '2d', terrain: { source: 'dem-1', exaggeration: 1, elevation_ref: 'EGM96' } },
      { pitch: 0 },
    ));
    expect(container.textContent).toContain('地形夸张系数：1×');
    expect(container.textContent).not.toContain('垂直比例夸张');
  });

  it('elevation_ref 缺席 → 高程基准：未声明', () => {
    const { container } = renderDisclosure(makeSpec(
      { mode: '2.5d', terrain: { source: 'dem-1', exaggeration: 1.2 } },
      { pitch: 0 },
    ));
    expect(container.textContent).toContain('高程基准：未声明');
  });

  it('透视 pitch（view.pitch > 阈值）→ 比例尺仅供参考（透视）警示徽标', () => {
    const { container } = renderDisclosure(makeSpec(
      { mode: '3d', terrain: { source: 'dem-1', elevation_ref: 'EGM96' }, camera: { pitch: 0 } },
      { pitch: 60 },
    ));
    expect(container.textContent).toContain('比例尺仅供参考（透视）');
    const badge = container.querySelector('[data-scale-trust="perspective"]');
    expect(badge).toBeTruthy();
  });

  it('view.pitch 缺席 → 回退 scene.camera.pitch 判定透视', () => {
    const { container } = renderDisclosure(makeSpec(
      { mode: '3d', terrain: { source: 'dem-1', elevation_ref: 'EGM96' }, camera: { pitch: 45 } },
    ));
    expect(container.querySelector('[data-scale-trust="perspective"]')).toBeTruthy();
  });

  it('可靠 pitch（≤ 阈值）→ 不出比例尺徽标', () => {
    const { container } = renderDisclosure(makeSpec(
      { mode: '2d', camera: { pitch: 0 } },
      { pitch: 0 },
    ));
    expect(container.querySelector('[data-scale-trust]')).toBeNull();
    expect(container.textContent).not.toContain('比例尺仅供参考');
  });

  it('pitch 未知（view 与 camera 均缺席）→ 比例尺可信度未知', () => {
    const { container } = renderDisclosure(makeSpec(
      { mode: '3d', terrain: { source: 'dem-1', elevation_ref: 'EGM96' } },
    ));
    expect(container.textContent).toContain('比例尺可信度未知');
    expect(container.querySelector('[data-scale-trust="unknown"]')).toBeTruthy();
  });

  it('无 scene 块 → 面板缺席（不虚构场景状态）', () => {
    const { container } = renderDisclosure(makeSpec(undefined, { pitch: 60 }));
    expect(container.querySelector('[data-testid="spec-chrome-scene-disclosure"]')).toBeNull();
    expect(container.textContent).toBe('');
  });

  it('spec 缺席（ctx.spec=null）→ 同样面板缺席', () => {
    const { container } = renderDisclosure(null);
    expect(container.querySelector('[data-testid="spec-chrome-scene-disclosure"]')).toBeNull();
  });

  it('options.extraNotes 逐行追加', () => {
    const { container } = renderDisclosure(
      makeSpec({ mode: '3d', camera: { pitch: 0 } }, { pitch: 0 }),
      { extraNotes: ['DEM 源：SRTM 30m', '坡度分析受分辨率限制'] },
    );
    expect(container.textContent).toContain('DEM 源：SRTM 30m');
    expect(container.textContent).toContain('坡度分析受分辨率限制');
  });

  it('compact variant → data-variant=compact', () => {
    const { container } = renderDisclosure(
      makeSpec({ mode: '3d', camera: { pitch: 0 } }, { pitch: 0 }),
      { variant: 'compact' },
    );
    expect(
      container.querySelector('[data-testid="spec-chrome-scene-disclosure"]')
        ?.getAttribute('data-variant'),
    ).toBe('compact');
  });
});
