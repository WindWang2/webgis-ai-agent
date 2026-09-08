/**
 * Workbench V4（Wave 7）：typed StyleIntent 词表与 applier 测试。
 * 锁定：封闭词表校验、相对意图（lighten/thinner）求值、钳制、lock 护栏、
 * spec 承载层的持久通道委托。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import {
  evaluateStyleIntent,
  applyStyleIntent,
  shiftLightness,
  hexToHsl,
  STYLE_PALETTES,
  type StyleIntent,
} from './style-intent';
import type { Layer, LayerStyle } from '@/lib/types/layer';

vi.mock('@/lib/mapspec/user-mutation', () => ({
  commitLayerStyleAndCommit: vi.fn(async () => {}),
}));

import { commitLayerStyleAndCommit } from '@/lib/mapspec/user-mutation';

describe('style intent · 色彩运算', () => {
  it('hex ↔ HSL 往返稳定', () => {
    for (const hex of ['#15803d', '#ffffff', '#000000', '#16a34a']) {
      const [h, s, l] = hexToHsl(hex);
      expect(h).toBeTypeOf('number');
      expect(s).toBeGreaterThanOrEqual(0);
      expect(l).toBeGreaterThanOrEqual(0);
      expect(l).toBeLessThanOrEqual(1);
    }
  });

  it('lighten 提升亮度、darken 降低；极值被钳制', () => {
    const base = '#15803d';
    const lighter = shiftLightness(base, 0.2);
    const darker = shiftLightness(base, -0.2);
    expect(hexToHsl(lighter)[2]).toBeGreaterThan(hexToHsl(base)[2]);
    expect(hexToHsl(darker)[2]).toBeLessThan(hexToHsl(base)[2]);
    // 极端 amount 不产生非法色值
    expect(shiftLightness(base, 5)).toMatch(/^#[0-9a-f]{6}$/);
    expect(shiftLightness(base, -5)).toMatch(/^#[0-9a-f]{6}$/);
  });
});

describe('style intent · evaluateStyleIntent', () => {
  const base: LayerStyle = { color: '#15803d', strokeWidth: 2, opacity: 0.8 };

  it('set_color 拒绝非 hex；set_palette 只认目录词表', () => {
    expect(evaluateStyleIntent(base, { kind: 'set_color', color: 'red' })).toBeNull();
    expect(evaluateStyleIntent(base, { kind: 'set_color', color: '#3b82f6' })?.color).toBe('#3b82f6');
    // Review R1 CRITICAL：palette/classification 无前端消费面 → 诚实失败
    // （词表保留作合约预埋，后端通道就绪前 agent 收到 failed 而非静默无效）。
    expect(evaluateStyleIntent(base, { kind: 'set_palette', palette: 'Blues' })).toBeNull();
    expect(STYLE_PALETTES.has('Blues')).toBe(true);
  });

  it('相对意图：thinner/thicker 按比例求值并钳制', () => {
    expect(evaluateStyleIntent(base, { kind: 'thinner' })?.strokeWidth).toBe(1.5); // 2 × 0.75
    expect(evaluateStyleIntent(base, { kind: 'thicker' })?.strokeWidth).toBe(2.5); // 2 × 1.25
    // 已是 0.2 的细线继续变细 → 钳到下限 0.2
    expect(evaluateStyleIntent({ strokeWidth: 0.2 }, { kind: 'thinner' })?.strokeWidth).toBe(0.2);
  });

  it('set_classification 诚实失败（后端 reclassify 通道未接线）', () => {
    // 方法/级数校验会在通道接线后恢复；当前一律 null → agent 收到 failed。
    expect(
      evaluateStyleIntent(base, { kind: 'set_classification', method: 'quantiles', classes: 7 }),
    ).toBeNull();
  });

  it('set_opacity / set_point_size 钳制到合理域', () => {
    expect(evaluateStyleIntent(base, { kind: 'set_opacity', opacity: 1.7 })?.opacity).toBe(1);
    expect(evaluateStyleIntent(base, { kind: 'set_point_size', size: 100 })?.pointSize).toBe(40);
  });

  it('未知意图 kind 返回 null（不猜近似）', () => {
    expect(evaluateStyleIntent(base, { kind: 'make_it_pretty' } as unknown as StyleIntent)).toBeNull();
  });
});

function mkLayer(id: string, patch: Partial<Layer> = {}): Layer {
  return { id, name: id, type: 'vector', visible: true, opacity: 1, style: { color: '#15803d' }, ...patch } as Layer;
}

describe('style intent · applyStyleIntent（applier 收口）', () => {
  beforeEach(() => {
    useHudStore.getState().clearLayers();
    vi.clearAllMocks();
  });

  it('应用意图：乐观 store 更新 + spec 层委托持久通道', async () => {
    useHudStore.getState().addLayer(mkLayer('a', { _mapspecLayerId: 'spec-a' }));
    const outcome = await applyStyleIntent('a', { kind: 'set_color', color: '#1d4ed8' });
    expect(outcome).toBe('applied');
    expect(useHudStore.getState().layers[0].style?.color).toBe('#1d4ed8');
    expect(vi.mocked(commitLayerStyleAndCommit)).toHaveBeenCalledWith('a', expect.objectContaining({ color: '#1d4ed8' }));
  });

  it('锁定层拒绝意图（lock 护栏），HUD-only 层不提交持久通道', async () => {
    useHudStore.getState().addLayer(mkLayer('locked-layer'));
    useHudStore.getState().toggleLayerLocked('locked-layer');
    expect(await applyStyleIntent('locked-layer', { kind: 'lighten' })).toBe('locked');

    useHudStore.getState().addLayer(mkLayer('hud-only'));
    expect(await applyStyleIntent('hud-only', { kind: 'lighten' })).toBe('applied');
    expect(vi.mocked(commitLayerStyleAndCommit)).not.toHaveBeenCalled();
  });

  it('非法意图返回 invalid 且不写 store', async () => {
    useHudStore.getState().addLayer(mkLayer('a'));
    const before = useHudStore.getState().layers[0].style;
    expect(await applyStyleIntent('a', { kind: 'set_color', color: 'blue' })).toBe('invalid');
    expect(useHudStore.getState().layers[0].style).toBe(before);
  });
});
