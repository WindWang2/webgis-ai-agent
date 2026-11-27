import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TopologyWorkbenchPanel } from './topology-workbench-panel';

const api = vi.hoisted(() => ({
  fetchRulesets: vi.fn(),
  fetchRuns: vi.fn(),
  fetchRun: vi.fn(),
  fetchFindings: vi.fn(),
}));
vi.mock('@/lib/api/topology-workbench', () => ({
  fetchRulesets: (...a: unknown[]) => api.fetchRulesets(...a),
  fetchRuns: (...a: unknown[]) => api.fetchRuns(...a),
  fetchRun: (...a: unknown[]) => api.fetchRun(...a),
  fetchFindings: (...a: unknown[]) => api.fetchFindings(...a),
  acceptException: vi.fn(),
  createRuleset: vi.fn(),
  fetchFindingWitness: vi.fn(),
  fetchRepairCandidates: vi.fn(),
  recheckRun: vi.fn(),
  revokeException: vi.fn(),
  submitTopologyRun: vi.fn(),
}));

vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: unknown) => unknown) => selector({ addToast: vi.fn() }),
}));
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: unknown) => unknown) => selector({ addLayer: vi.fn() }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchRuns.mockResolvedValue({ items: [] });
});

describe('TopologyWorkbenchPanel error lifecycle', () => {
  it('一次加载失败后，刷新成功会清除常驻错误横幅', async () => {
    api.fetchRulesets
      .mockRejectedValueOnce(new Error('rulesets load boom'))
      .mockResolvedValueOnce({ items: [] });
    render(<TopologyWorkbenchPanel projectId="p1" sessionId="s1" authed />);

    expect(await screen.findByText(/rulesets load boom/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /刷新/ }));
    await waitFor(() => expect(screen.queryByText(/rulesets load boom/)).toBeNull());
  });
});
