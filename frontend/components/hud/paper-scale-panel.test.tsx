/**
 * GOAL 04 WP5 —— 纸面比例尺综合面板 smoke：预览回执渲染（QA 摘要/管线）、
 * commit 冻结流、qa_blocked 如实呈现。API 层 mock（业务真相在服务端，
 * 由 tests/unit/gis_harness/paper_generalization 覆盖）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { PaperScalePanel } from './paper-scale-panel';

const postMock = vi.fn();

vi.mock('@/lib/api/transport', () => ({
  apiFetch: (...args: unknown[]) => postMock(...(args as [never, never])),
}));
vi.mock('@/lib/i18n/useT', () => ({
  useT: () => (key: string, params?: Record<string, unknown>) => {
    const template: Record<string, string> = {
      qaSummary: `QA {p0} fail {p1}`,
      counts: `after {p0}/original {p1}`,
      frozen: `frozen {p0}`,
      qaRefused: 'qaRefused',
      previewOk: 'previewOk',
      qaBlocked: `qaBlocked {p0}`,
      notApplied: `notApplied {p0}`,
      failed: `failed {p0}`,
    };
    let out = template[key] ?? key;
    for (const [k, v] of Object.entries(params ?? {})) {
      out = out.replace(`{${k}}`, String(v));
    }
    return out;
  },
  useLocale: () => 'zh-CN',
}));
const specHolder: { spec: unknown } = { spec: null };
vi.mock('@/lib/mapspec/session-cursor', () => ({
  getCommittedMapSpec: () => specHolder.spec,
  getMapSpecSessionCursor: () => ({ sessionId: 'sess-1', revision: 7 }),
  subscribeMapSpecLive: () => () => {},
}));
vi.mock('@/lib/mapspec/user-mutation', () => ({
  refreshMapSpecAfterExternalMutation: vi.fn(),
}));

const previewReceipt = {
  status: 'preview',
  pipeline: [
    { step: 'eliminate_small_polygons', eliminated: 1 },
    { step: 'topology_simplify', simplified: 2 },
  ],
  stats: { original_count: 3, artifact_count: 2 },
  qa_summary: { total: 6, blocking: 0 },
  semantic_checks: [],
  disclosures: [],
};

beforeEach(() => {
  postMock.mockReset();
});

describe('PaperScalePanel', () => {
  it('renders form and frozen-plan list from committed spec', () => {
    // 冻结清单经 committed spec 注入（mock holder 可变态）
    specHolder.spec = {
      layers: [{
        id: 'districts',
        scale_profile: { paper_plans: {
          'psp-1': {
            status: 'frozen',
            contract: { target_denominator: 500000, purpose: 'overview' },
            stats: { original_count: 3, artifact_count: 2 },
            qa_summary: { blocking: 0 },
            artifact_ref: 'ref:pgen1',
          },
        } },
      }],
    };
    render(<PaperScalePanel layerId="districts" />);
    expect(screen.getByTestId('paper-plans')).toBeTruthy();
    expect(screen.getByText(/500,000/).textContent).toContain('overview');
  });

  it('preview shows QA summary and pipeline stats', async () => {
    postMock.mockResolvedValueOnce(previewReceipt);
    render(<PaperScalePanel layerId="districts" />);
    fireEvent.click(screen.getByTestId('paper-preview'));
    await waitFor(() => {
      expect(screen.getByTestId('paper-qa').textContent).toContain('fail 0');
    });
    expect(postMock).toHaveBeenCalledWith(
      '/api/v1/chat/sessions/sess-1/paper-scale',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          layer: 'districts',
          denominator_text: '1:50万',
          page_text: 'A4横版',
          purpose: 'overview',
          mode: 'preview',
        }),
      }),
    );
    expect(screen.getByTestId('paper-message').textContent).toBe('previewOk');
  });

  it('commit success shows frozen receipt and refreshes', async () => {
    postMock.mockResolvedValueOnce({ status: 'committed',
      mapspec_revision: 12, artifact_ref: 'ref:pgen2' });
    render(<PaperScalePanel layerId="districts" />);
    fireEvent.click(screen.getByTestId('paper-commit'));
    await waitFor(() => {
      expect(screen.getByTestId('paper-message').textContent).toContain('12');
    });
  });

  it('qa_blocked is presented honestly, not as success', async () => {
    postMock.mockResolvedValueOnce({
      status: 'qa_blocked',
      qa_summary: { total: 6, blocking: 2 },
      semantic_checks: [],
    });
    render(<PaperScalePanel layerId="districts" />);
    fireEvent.click(screen.getByTestId('paper-commit'));
    await waitFor(() => {
      expect(screen.getByTestId('paper-message').textContent)
        .toBe('qaRefused');
    });
  });

  it('api failure surfaces error message', async () => {
    postMock.mockRejectedValueOnce(new Error('boom'));
    render(<PaperScalePanel layerId="districts" />);
    fireEvent.click(screen.getByTestId('paper-preview'));
    await waitFor(() => {
      expect(screen.getByTestId('paper-message').textContent)
        .toContain('boom');
    });
  });
});
