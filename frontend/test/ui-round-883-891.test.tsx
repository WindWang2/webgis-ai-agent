/**
 * UI 专项回归（U-1..U-9 / #883-#891）。
 *
 * - U-1: 样式编辑入口可达（Palette 按钮 + ContextPanel 钻入）
 * - U-2: colorbar 与 scale_bar 同槽堆叠不重叠
 * - U-3: 非 409 提交失败回滚必 toast
 * - U-9: aria-selected 只表达真实选中
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

import { useHudStore } from '@/lib/store/useHudStore';

// ─── U-1: 样式编辑入口 ────────────────────────────────────────────────────

describe('U-1 (#883) 图层样式编辑入口可达', () => {
  beforeEach(() => {
    useHudStore.getState().setEditingLayerId(null);
    useHudStore.setState({
      layers: [
        {
          id: 'layer-1', name: '测试图层', visible: true, opacity: 1,
          source: { type: 'FeatureCollection', features: [] },
        } as never,
      ],
    });
  });

  it('layers-tab 图层行渲染「编辑图层样式」按钮且写入 editingLayerId', async () => {
    const { LayersTab } = await import('@/components/sidebar/layers-tab');
    render(<LayersTab />);
    const btn = screen.getByLabelText('编辑图层样式 测试图层');
    expect(btn).toBeTruthy();
    fireEvent.click(btn);
    expect(useHudStore.getState().editingLayerId).toBe('layer-1');
  });

  it('context-panel 源码锚点：editingLayerId 驱动 LayerStylePanel 钻入', async () => {
    const fs = await import('fs');
    const src = fs.readFileSync('components/layout/context-panel.tsx', 'utf-8');
    expect(src).toContain("editingLayerId ? <LayerStylePanel /> : <LayersTab />");
  });

  it('settings 源码锚点：图层管理面板进入导航', async () => {
    const fs = await import('fs');
    const src = fs.readFileSync('components/settings/settings-panel.tsx', 'utf-8');
    expect(src).toContain("key: 'layers'");
    expect(src).toContain('<LayerManagement />');
  });
});

// ─── U-2: 同槽堆叠（纯逻辑：jsdom 会丢弃含 var() 的 calc 内联值） ──────

describe('U-2 (#884) colorbar 与 scale_bar 同槽堆叠', () => {
  it('同 bottom-right 双组件：scale_bar 贴底(0 层)，colorbar 上移(1 层)', async () => {
    const mod = await import('@/components/map/map-spec-chrome');
    const cb = { id: 'cb', type: 'continuous_colorbar', enabled: true } as never;
    const sb = { id: 'sb', type: 'scale_bar', enabled: true } as never;
    // spec 序 colorbar 在前也不能让 scale_bar 被挤到高层
    const idx = mod.buildBottomSlotIndexes([cb, sb]);
    expect(idx.get(sb as never)).toBe(0);
    expect(idx.get(cb as never)).toBe(1);
    const sbStyle = mod.stackedBottomStyle(sb as never, idx);
    const cbStyle = mod.stackedBottomStyle(cb as never, idx);
    expect(sbStyle?.bottom).not.toEqual(cbStyle?.bottom);
    expect(String(cbStyle?.bottom)).toMatch(/66px/);
  });

  it('单组件槽不产生偏移变化（保持既有 +30 行为）', async () => {
    const mod = await import('@/components/map/map-spec-chrome');
    const sb = { id: 'sb', type: 'scale_bar', enabled: true } as never;
    const idx = mod.buildBottomSlotIndexes([sb]);
    expect(idx.size).toBe(0);
    expect(mod.stackedBottomStyle(sb as never, idx)?.bottom).toContain('30px');
  });
});

// ─── U-3: 回滚 toast ──────────────────────────────────────────────────────

describe('U-3 (#885) 非 409 提交失败回滚必 toast', () => {
  it('removeLayerAndCommit 失败（非 409）时调用 addToast', async () => {
    const apiMod = await import('@/lib/api/transport');
    vi.spyOn(apiMod, 'apiFetch').mockRejectedValue(new Error('network down'));
    const toastMod = await import('@/components/ui/toast');
    const addToast = vi.spyOn(toastMod.useToastStore.getState(), 'addToast').mockImplementation(() => {});

    useHudStore.setState({
      layers: [
        { id: 'layer-x', name: 'X', visible: true, opacity: 1 } as never,
      ],
    });
    // 提供 mapspec 会话游标，避免因无 session 提前返回
    const cursor = await import('@/lib/mapspec/session-cursor');
    const spy = vi.spyOn(cursor, 'getMapSpecSessionCursor').mockReturnValue({
      sessionId: 'sess-u3', revision: 1, ownerToken: 'tok',
    } as never);

    const { removeLayerAndCommit } = await import('@/lib/mapspec/user-mutation');
    await removeLayerAndCommit('layer-x');

    await waitFor(() => {
      expect(addToast).toHaveBeenCalled();
    });
    expect(useHudStore.getState().layers.find((l) => l.id === 'layer-x')).toBeTruthy();
    addToast.mockRestore();
    spy.mockRestore();
  });
});
