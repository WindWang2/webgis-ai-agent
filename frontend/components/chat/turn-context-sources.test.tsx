/**
 * G43（ADR-0295）：会话可选资料工作台测试 —— 优先/排除/CAS 冲突（C12）。
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { TurnContextSources } from '@/components/chat/turn-context-sources';
import {
  fetchOptionalSources,
  fetchSourcePolicy,
  putSourcePolicy,
  PolicyConflictError,
} from '@/lib/api/turnContext';

vi.mock('@/lib/api/turnContext', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/turnContext')>(
    '@/lib/api/turnContext');
  return {
    ...actual,
    fetchOptionalSources: vi.fn(),
    fetchSourcePolicy: vi.fn(),
    putSourcePolicy: vi.fn(),
    postContextPreview: vi.fn(),
  };
});
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (sel: (s: { activeProjectId: string | null }) => unknown) =>
    sel({ activeProjectId: 'p1' }),
}));

const mockSources = {
  sources: [
    { source_id: 'pk:1', kind: 'project_knowledge_entry', label: '2025 数据集', version_token: 'tok-a', selectable: true },
    { source_id: 'cm:2', kind: 'carto_memory_fact', label: 'preference · 配色', version_token: 'lv:1', selectable: true },
    { source_id: 'gm:block', kind: 'gis_memory_block', label: 'GIS 空间记忆（整块）', version_token: '', selectable: true },
  ],
  counts: {},
};

describe('TurnContextSources', () => {
  beforeEach(() => {
    vi.mocked(fetchSourcePolicy).mockResolvedValue({
      session_id: 's1',
      policy: { mode: 'auto', preferred: [], excluded: [], policy_version: 3 },
    });
    vi.mocked(fetchOptionalSources).mockResolvedValue(mockSources as never);
    vi.mocked(putSourcePolicy).mockReset();
  });

  it('renders authorized catalog with server-minted ids and per-source toggles', async () => {
    render(<TurnContextSources sessionId="s1" />);
    fireEvent.click(screen.getByTestId('turn-context-sources-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('turn-source-pk:1')).toBeTruthy();
    });
    expect(screen.getByTestId('turn-source-cm:2')).toBeTruthy();
    expect(screen.getByTestId('turn-source-gm:block')).toBeTruthy();
  });

  it('excludes a source, saves via CAS, and surfaces conflict on 409', async () => {
    render(<TurnContextSources sessionId="s1" />);
    fireEvent.click(screen.getByTestId('turn-context-sources-toggle'));
    await waitFor(() => {
      expect(screen.getByTestId('turn-source-cm:2')).toBeTruthy();
    });
    // 第一次保存成功（服务端返回新版本）
    vi.mocked(putSourcePolicy).mockResolvedValueOnce({
      session_id: 's1',
      policy: { mode: 'explicit', preferred: [], excluded: ['cm:2'], policy_version: 4 },
    });
    // 排除 cm:2（三态按钮组）
    fireEvent.click(screen.getByTestId('turn-source-cm:2-excluded'));
    fireEvent.click(screen.getByTestId('turn-sources-save'));
    await waitFor(() => {
      expect(putSourcePolicy).toHaveBeenCalledWith(
        's1',
        expect.objectContaining({
          mode: 'explicit',
          preferred: [],
          excluded: ['cm:2'],
          expected_version: 3,
          project_id: 'p1',
        }),
        undefined,
      );
    });
    // 第二轮：另一窗口并发更新 → 409 冲突展示当前值 + 合并提示（C12）
    vi.mocked(putSourcePolicy).mockReset();
    vi.mocked(putSourcePolicy).mockRejectedValueOnce(
      new PolicyConflictError({
        mode: 'explicit', preferred: ['pk:1'], excluded: [],
        policy_version: 5,
      }),
    );
    fireEvent.click(screen.getByTestId('turn-source-pk:1-preferred'));
    fireEvent.click(screen.getByTestId('turn-sources-save'));
    await waitFor(() => {
      expect(screen.getByTestId('turn-sources-conflict')).toBeTruthy();
    });
  });

  it('hides entirely without a session', () => {
    const { container } = render(<TurnContextSources sessionId={null} />);
    expect(container.firstChild).toBeNull();
  });
});
