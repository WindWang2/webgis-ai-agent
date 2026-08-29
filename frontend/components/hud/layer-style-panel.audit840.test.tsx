/**
 * audit #840/#841/#842: LayerStylePanel honesty on spec-backed layers.
 *
 * - committed MapSpec layers: style controls disabled with an explanation
 *   (previously silent no-ops); palette/intensity controls removed entirely
 *   (zero consumers on any path); radius slider clamped to the contract
 *   window [4,80].
 * - commitOpacity routes through the rollback wrapper instead of a bare
 *   void commit (no unhandled rejections / state divergence).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LayerStylePanel } from './layer-style-panel';

const mockState = {
  editingLayerId: 'layer-1' as string | null,
  layers: [
    {
      id: 'layer-1',
      name: 'POI 层',
      type: 'vector',
      visible: true,
      opacity: 0.8,
      style: { color: '#00f2ff', radius_px: 30 },
    },
  ],
  updateLayer: vi.fn(),
  setEditingLayerId: vi.fn(),
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
}));

const cursorListeners = new Set<() => void>();
let committedSpec: unknown = null;

vi.mock('@/lib/mapspec/session-cursor', () => ({
  getCommittedMapSpec: () => committedSpec,
  subscribeMapSpecLive: (cb: () => void) => {
    cursorListeners.add(cb);
    return () => cursorListeners.delete(cb);
  },
}));

const opacityCommit = vi.fn();
vi.mock('@/lib/mapspec/user-mutation', () => ({
  setLayerOpacityAndCommit: (...args: unknown[]) => opacityCommit(...args),
  commitLayerStyleAndCommit: vi.fn(async () => {}),
}));

const heatContractWindow = vi.fn(async () => {
  // mirror assertion source: the adapter must use the 4-60 legacy window
  const src = await import('@/lib/mapspec-runtime/adapter');
  const text = (await import('@/lib/mapspec-runtime/adapter?raw')).default as string;
  return { src, text };
});

beforeEach(() => {
  vi.clearAllMocks();
  committedSpec = null;
  cursorListeners.clear();
});

describe('audit #840: spec-backed gating', () => {
  it('shows style controls when no committed spec exists', () => {
    render(<LayerStylePanel />);
    expect(screen.getByLabelText('填充颜色')).toBeTruthy();
    expect(screen.queryByText(/由 AI 生成的制图规范/)).toBeNull();
  });

  it('#1077 (v2): spec-backed layers expose durable canonical controls; filter controls stay gated', () => {
    committedSpec = { layers: [{ id: 'layer-1' }] };
    render(<LayerStylePanel />);
    // 说明文案更新：规范样式修改持久提交（#1077 durable 通道）
    expect(screen.getByText(/持久提交到地图规范/)).toBeTruthy();
    // 规范键控件（颜色等）启用 —— durable 通道经 patch_layer_style 写权威 spec
    const color = screen.getByLabelText('填充颜色') as HTMLInputElement;
    const fieldset = color.closest('fieldset') as HTMLFieldSetElement;
    expect(fieldset.disabled).toBe(false);
    // opacity 独立通道：滑杆存在且启用（presentation mutation，非样式面）
    const sliders = screen.getAllByRole('slider') as HTMLInputElement[];
    const opacity = sliders[sliders.length - 1];
    expect(opacity).toBeTruthy();
    expect(opacity.disabled).toBe(false);
  });

  it('removes the never-consumed palette/intensity controls', () => {
    mockState.layers[0].type = 'heatmap';
    render(<LayerStylePanel />);
    expect(screen.queryByText('色带')).toBeNull();
    expect(screen.queryByText('热力强度')).toBeNull();
    // radius slider survives (has real consumers)
    expect(screen.getByText(/热力半径/)).toBeTruthy();
    mockState.layers[0].type = 'vector';
  });
});

describe('audit #842: opacity commit via rollback wrapper', () => {
  it('commits opacity through setLayerOpacityAndCommit', () => {
    render(<LayerStylePanel />);
    const sliders = screen.getAllByRole('slider');
    const opacitySlider = sliders[sliders.length - 1];
    fireEvent.pointerUp(opacitySlider);
    expect(opacityCommit).toHaveBeenCalled();
  });
});

describe('audit #841: radius windows', () => {
  it('adapter legacy window matches the contract (4-60)', async () => {
    const { text } = await heatContractWindow();
    expect(text).toContain('legacyPx >= 4 && legacyPx <= 60');
    expect(text).not.toContain('legacyPx >= 4 && legacyPx <= 100');
    void text; void (await import('@/lib/mapspec-runtime/adapter'));
  });

  it('slider range is clamped to [4,80]', () => {
    mockState.layers[0].type = 'heatmap';
    render(<LayerStylePanel />);
    const radiusLabel = screen.getByText(/热力半径/);
    const radiusSlider = radiusLabel.parentElement!.querySelector('input[type="range"]') as HTMLInputElement;
    expect(radiusSlider.min).toBe('4');
    expect(radiusSlider.max).toBe('80');
    mockState.layers[0].type = 'vector';
  });
});

describe('#1077 aliased spec-backed rows (runtimePatch mirror)', () => {
  it('rows whose id is a geojson ref but _mapspecLayerId matches the spec route edits to the spec layer', () => {
    // runtimePatch 挂载行：id 是 ref，_mapspecLayerId 才是 spec 层 id ——
    // 精确 id 匹配会把这类行误判为非 specBacked（样式控件可用但 compose
    // 不消费 = 静默 no-op）。v2 守卫语义：行被识别为 spec-backed 后，
    // 规范键控件启用且经 durable 通道按 _mapspecLayerId 提交到 spec 层；
    // 非规范滤镜控件保持禁用 —— 不再整组 fieldset 禁用。
    mockState.editingLayerId = 'ref:geojson-abc';
    (mockState.layers as any)[0] = {
      ...mockState.layers[0],
      id: 'ref:geojson-abc',
      _mapspecLayerId: 'poi-main',
    };
    committedSpec = { layers: [{ id: 'poi-main' }], sources: {} };
    render(<LayerStylePanel />);
    const fieldset = document.querySelector('fieldset');
    expect(fieldset?.disabled).toBe(false);
    // 规范键控件（填充颜色）启用 —— durable 提交按 spec 层键路由
    const color = screen.getByLabelText('填充颜色') as HTMLInputElement;
    expect(color.disabled).toBe(false);
    // 滤镜类控件（线型，规范未建模）保持禁用
    const dashButton = screen.getByText('虚线') as HTMLButtonElement;
    expect(dashButton.disabled).toBe(true);
    // restore shared mock state
    mockState.editingLayerId = 'layer-1';
    (mockState.layers as any)[0] = {
      id: 'layer-1', name: 'POI 层', type: 'vector', visible: true,
      opacity: 0.8, style: { color: '#00f2ff', radius_px: 30 },
    };
    committedSpec = null;
  });
});
