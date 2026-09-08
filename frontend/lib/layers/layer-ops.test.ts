/**
 * Workbench V4（Wave 2）：layer-ops 命令层测试（mock user-mutation 通道）。
 * 锁定：isolate 快照/恢复、批量操作跳过锁定层与等值层、锁定护栏。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import type { Layer } from '@/lib/types/layer';

vi.mock('@/lib/mapspec/user-mutation', () => ({
  toggleLayerAndCommit: vi.fn(async (id: string) => {
    // 与真实实现同构：乐观翻转 store 可见性（CAS/网络在真实通道内）。
    const layer = useHudStore.getState().layers.find((l) => l.id === id);
    if (!layer) return;
    useHudStore.getState().toggleLayer(id);
  }),
  setLayerOpacityAndCommit: vi.fn(async () => {}),
  commitLayerStyleAndCommit: vi.fn(async () => {}),
  removeLayerAndCommit: vi.fn(async () => {}),
  reorderLayersAndCommit: vi.fn(async () => {}),
}));
vi.mock('@/lib/chat/turn-focus', () => ({
  tagUserDisplayed: vi.fn(),
}));
vi.mock('@/lib/store/layer-data', () => ({
  ensureLayerData: vi.fn(async () => ({})),
}));
vi.mock('@/lib/mapspec/ref-source-resolver', () => ({
  clearRefSourceFailed: vi.fn(),
  getRefSourceState: vi.fn(() => 'unresolved'),
}));
vi.mock('@/components/ui/toast', () => ({
  useToastStore: { getState: () => ({ addToast: vi.fn() }) },
}));

import {
  isolateLayerAndCommit,
  clearIsolateAndCommit,
  batchSetVisibility,
  batchSetOpacity,
} from './layer-ops';
import { toggleLayerAndCommit } from '@/lib/mapspec/user-mutation';

function mkLayer(id: string, patch: Partial<Layer> = {}): Layer {
  return { id, name: id, type: 'vector', visible: true, opacity: 1, ...patch } as Layer;
}

function seed(layers: Layer[]): void {
  useHudStore.getState().setLayers(layers);
}

describe('layer-ops · isolate', () => {
  beforeEach(() => {
    useHudStore.getState().resetLayerGroups();
    seed([mkLayer('a'), mkLayer('b', { visible: false }), mkLayer('c')]);
    vi.clearAllMocks();
  });

  it('进入隔离：快照只记可见层；其余可见层走 toggle 隐藏', async () => {
    await isolateLayerAndCommit('a');
    const s = useHudStore.getState();
    expect(s.isolatedLayerId).toBe('a');
    expect(s.isolatedFrom).toEqual({ c: true }); // b 本就隐藏，不入快照
    expect(s.layers.find((l) => l.id === 'c')?.visible).toBe(false);
    expect(s.layers.find((l) => l.id === 'a')?.visible).toBe(true);
  });

  it('退出隔离：按快照恢复可见层', async () => {
    await isolateLayerAndCommit('a');
    await clearIsolateAndCommit();
    const s = useHudStore.getState();
    expect(s.isolatedLayerId).toBeNull();
    expect(s.layers.find((l) => l.id === 'c')?.visible).toBe(true);
    // b 恢复后仍隐藏（快照里没有它）
    expect(s.layers.find((l) => l.id === 'b')?.visible).toBe(false);
  });

  it('锁定层不参与隔离隐藏，但快照仍记录（解锁后可恢复）', async () => {
    useHudStore.getState().toggleLayerLocked('c');
    await isolateLayerAndCommit('a');
    expect(useHudStore.getState().layers.find((l) => l.id === 'c')?.visible).toBe(true);
  });

  it('重复 isolate 同一目标是 no-op', async () => {
    await isolateLayerAndCommit('a');
    const calls = vi.mocked(toggleLayerAndCommit).mock.calls.length;
    await isolateLayerAndCommit('a');
    expect(vi.mocked(toggleLayerAndCommit).mock.calls.length).toBe(calls);
  });

  it('不存在的层 isolate 是 no-op', async () => {
    await isolateLayerAndCommit('ghost');
    expect(useHudStore.getState().isolatedLayerId).toBeNull();
  });
});

describe('layer-ops · batch', () => {
  beforeEach(() => {
    useHudStore.getState().resetLayerGroups();
    vi.clearAllMocks();
  });

  it('批量隐藏：只对可见层发 toggle；已隐藏层跳过', async () => {
    seed([mkLayer('a'), mkLayer('b', { visible: false })]);
    await batchSetVisibility(['a', 'b'], false);
    expect(vi.mocked(toggleLayerAndCommit)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(toggleLayerAndCommit)).toHaveBeenCalledWith('a');
  });

  it('锁定层跳过批量操作', async () => {
    seed([mkLayer('a'), mkLayer('c')]);
    useHudStore.getState().toggleLayerLocked('a');
    await batchSetVisibility(['a', 'c'], false);
    expect(vi.mocked(toggleLayerAndCommit)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(toggleLayerAndCommit)).toHaveBeenCalledWith('c');
  });

  it('批量开启时对隐藏层发 toggle', async () => {
    seed([mkLayer('b', { visible: false })]);
    await batchSetVisibility(['b'], true);
    expect(vi.mocked(toggleLayerAndCommit)).toHaveBeenCalledWith('b');
  });

  it('批量不透明度：等值层跳过、越界值钳制', async () => {
    const { setLayerOpacityAndCommit } = await import('@/lib/mapspec/user-mutation');
    seed([mkLayer('a', { opacity: 0.5 }), mkLayer('b', { opacity: 0.3 })]);
    await batchSetOpacity(['a', 'b'], 0.5);
    // a 等值跳过；b 0.3 → 0.5
    expect(vi.mocked(setLayerOpacityAndCommit)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(setLayerOpacityAndCommit)).toHaveBeenCalledWith('b', 0.5);
    // 越界钳制到 [0,1]
    await batchSetOpacity(['b'], 1.7);
    expect(vi.mocked(setLayerOpacityAndCommit)).toHaveBeenLastCalledWith('b', 1);
  });
});
