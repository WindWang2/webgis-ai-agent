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
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
  // 子面板（G41 双变量/可靠性）读取同一游标。本套用例只校验 #840 分层门控，
  // 不建立会话 → sessionId 为空，子面板保持 idle（真实会话行为由
  // bivariate-reliability-panel.test.tsx 覆盖），避免向不存在的会话发请求。
  getMapSpecSessionCursor: () => ({
    sessionId: undefined,
    revision: 0,
    ownerToken: null,
    mapspecFingerprint: undefined,
  }),
}));

const opacityCommit = vi.fn();
vi.mock('@/lib/mapspec/user-mutation', () => ({
  setLayerOpacityAndCommit: (...args: unknown[]) => opacityCommit(...args),
  commitLayerStyleAndCommit: vi.fn(async () => {}),
}));

const frameGridState = { open: false, activeFrameId: null as string | null };
vi.mock('@/lib/cartography/frame-grid-store', () => ({
  getFrameGridSnapshot: () => frameGridState,
}));

const frameOverrideCommit = vi.fn();
vi.mock('@/lib/cartography/frame-edit-scope', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/lib/cartography/frame-edit-scope')>(),
  commitLayerOverrideForFrame: (...args: unknown[]) => frameOverrideCommit(...args),
}));

const toastAdd = vi.fn();
vi.mock('@/components/ui/toast', () => {
  const store = { addToast: (...args: unknown[]) => toastAdd(...args) };
  const useToastStore = Object.assign(
    (selector?: (s: typeof store) => unknown) => selector ? selector(store) : store,
    { getState: () => store },
  );
  return { useToastStore };
});

// 符号资产面板按 committed 层 id 精确寻址（G40）：用桩捕获实际传入的 layerId。
vi.mock('@/components/hud/symbol-asset-panel', () => ({
  default: ({ layerId }: { layerId: string }) => (
    <div data-testid="symbol-asset-stub" data-layer-id={layerId} />
  ),
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
  frameGridState.open = false;
  frameGridState.activeFrameId = null;
  frameOverrideCommit.mockResolvedValue('routed');
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

  it('disables stroke color on spec-backed line layers without a canonical key', () => {
    committedSpec = { layers: [{ id: 'layer-1', type: 'line' }], sources: {} };
    render(<LayerStylePanel />);
    expect(screen.getByLabelText('描边颜色')).toBeDisabled();
    expect(screen.getByLabelText('填充颜色')).not.toBeDisabled();
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
    fireEvent.change(opacitySlider, { target: { value: '0.4' } });
    fireEvent.pointerUp(opacitySlider);
    expect(opacityCommit).toHaveBeenCalled();
  });

  it('does not report local success when the target frame is no longer active', async () => {
    frameGridState.open = true;
    frameGridState.activeFrameId = 'frame-1';
    frameOverrideCommit.mockResolvedValue('no_active_frame');
    render(<LayerStylePanel />);
    const sliders = screen.getAllByRole('slider');
    const opacitySlider = sliders[sliders.length - 1];
    fireEvent.change(opacitySlider, { target: { value: '0.4' } });
    fireEvent.pointerUp(opacitySlider);

    await waitFor(() => expect(toastAdd).toHaveBeenCalled());
    expect(frameOverrideCommit).toHaveBeenCalledWith('frame-1', expect.objectContaining({
      override: { opacity: 0.4 },
    }));
    expect(mockState.updateLayer).not.toHaveBeenCalled();
    expect(opacityCommit).not.toHaveBeenCalled();
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

describe('G40 symbol assets: alias rows target the hit layer id', () => {
  it('passes the committed hit id (not the geojson ref alias) for __-suffixed layers', () => {
    mockState.editingLayerId = 'ref:geojson-abc';
    (mockState.layers as any)[0] = {
      ...mockState.layers[0],
      id: 'ref:geojson-abc',
      _mapspecLayerId: undefined,
    };
    committedSpec = {
      layers: [{ id: 'ref:geojson-abc__main', type: 'circle' }],
      sources: {},
    };
    render(<LayerStylePanel />);
    // inspect/apply 走 committed 层精确 id —— 传别名会恒 UNKNOWN_LAYER
    expect(screen.getByTestId('symbol-asset-stub').getAttribute('data-layer-id'))
      .toBe('ref:geojson-abc__main');
    // restore shared mock state
    mockState.editingLayerId = 'layer-1';
    (mockState.layers as any)[0] = {
      id: 'layer-1', name: 'POI 层', type: 'vector', visible: true,
      opacity: 0.8, style: { color: '#00f2ff', radius_px: 30 },
    };
    committedSpec = null;
  });
});
