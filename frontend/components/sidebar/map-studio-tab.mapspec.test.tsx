import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import { commitMapSpecDocument, setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';

/**
 * PR-C：Map Studio 是主制图编辑面 —— 标题/副标题/指北针/比例尺/图例
 * 从 committed MapSpec 初始化、经 user mutation 写回；导出只用 MapSpec。
 * 写入函数在此 spy（其 HTTP/CAS 行为由 studio-layout.test.ts 覆盖）。
 */
const { dispatchAction, commitStudioText, commitStudioToggle } = vi.hoisted(() => ({
  dispatchAction: vi.fn(),
  commitStudioText: vi.fn((..._args: unknown[]) => Promise.resolve()),
  commitStudioToggle: vi.fn((..._args: unknown[]) => Promise.resolve()),
}));

const mockState: Record<string, any> = {
  exportSettings: {
    isExportMode: false,
    // 本地旧字段故意与 spec 不同：Studio/导出都不得再读它们
    title: '本地旧标题',
    subtitle: '',
    author: '',
    dataSource: '',
    showWatermark: true,
    showCompass: true,
    showScale: true,
    showLegend: true,
    showMetadata: true,
    showGraticules: false,
    paperSize: 'A4',
    orientation: 'landscape',
    dpi: 150,
    format: 'png',
  },
  updateExportSettings: vi.fn(),
  exports: [],
  setExports: vi.fn(),
  leftPanelOpen: true,
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector(mockState),
}));
vi.mock('@/lib/contexts/map-action-context', () => ({
  useMapAction: () => ({ dispatchAction }),
}));
vi.mock('@/lib/auth/use-auth-user', () => ({
  useAuthUser: () => ({ id: 'u1', username: 'tester' }),
}));
vi.mock('@/lib/mapspec/studio-layout', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/mapspec/studio-layout')>();
  return { ...actual, commitStudioText, commitStudioToggle };
});

import { MapStudioTab } from './map-studio-tab';

function spec(components: unknown[]): MapSpec {
  return { version: '1', layers: [], layout: { components } } as unknown as MapSpec;
}

beforeEach(() => {
  vi.clearAllMocks();
  setMapSpecSessionCursor('sid-studio-tab', 3);
  commitMapSpecDocument(spec([
    { id: 'title', type: 'title', enabled: true, options: { text: 'Spec 标题' } },
    { id: 'north-arrow', type: 'north_arrow', enabled: false },
  ]), 3);
});

describe('MapStudioTab ↔ MapSpec', () => {
  it('标题从 MapSpec 初始化（不读本地 exportSettings.title）', () => {
    render(<MapStudioTab />);
    expect(screen.getByLabelText('主标题')).toHaveValue('Spec 标题');
  });

  it('Agent 提交新 spec → 未编辑的标题输入同步为新真相', () => {
    render(<MapStudioTab />);
    act(() => {
      commitMapSpecDocument(spec([{ id: 'title', type: 'title', options: { text: 'Agent 改的标题' } }]), 4);
    });
    expect(screen.getByLabelText('主标题')).toHaveValue('Agent 改的标题');
  });

  it('编辑标题：输入期间不提交，失焦时经 user mutation 提交一次', () => {
    render(<MapStudioTab />);
    const input = screen.getByLabelText('主标题');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: '用户标题' } });
    expect(commitStudioText).not.toHaveBeenCalled();
    fireEvent.blur(input);
    expect(commitStudioText).toHaveBeenCalledTimes(1);
    expect(commitStudioText).toHaveBeenCalledWith('title', '用户标题');
  });

  it('指北针开关反映 spec（disabled）并写回 MapSpec；水印仍是本地参数', () => {
    render(<MapStudioTab />);
    fireEvent.click(screen.getByRole('button', { name: /地图元素/ }));
    const compass = screen.getByLabelText('指北针');
    expect(compass).not.toBeChecked();
    fireEvent.click(compass);
    expect(commitStudioToggle).toHaveBeenCalledWith('showCompass', true);
    fireEvent.click(screen.getByLabelText('AI 水印'));
    expect(commitStudioToggle).toHaveBeenCalledTimes(1);
    expect(mockState.updateExportSettings).toHaveBeenCalledWith({ showWatermark: false });
  });

  it('导出参数：制图内容取 MapSpec，DPI/纸张取本地', () => {
    render(<MapStudioTab />);
    fireEvent.click(screen.getByRole('button', { name: /发布并导出/ }));
    expect(dispatchAction).toHaveBeenCalledTimes(1);
    const { command, params } = dispatchAction.mock.calls[0][0];
    expect(command).toBe('export_map');
    expect(params).toMatchObject({
      title: 'Spec 标题', showCompass: false, showScale: true, showLegend: true,
      dpi: 150, paperSize: 'A4', format: 'png',
    });
    expect(params).not.toHaveProperty('isExportMode');
  });
});
