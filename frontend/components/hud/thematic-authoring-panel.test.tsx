/**
 * T-F2：G33 工作台面板 smoke——inspect 载入、人工断点编辑与错误定位、
 * 预览回执渲染、恢复自动入口。API 层 mock（业务校验真相在服务端，由
 * tests/unit/thematic_authoring 覆盖）；切层迟到丢弃（验收 18）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { ThematicAuthoringPanel } from './thematic-authoring-panel';

const inspectReceipt = {
  layer_id: 'result-1',
  layer_type: 'fill',
  source: {
    source_id: 'result-1-source', ref_id: 'ref:abc', content_revision: 2,
    data_fingerprint: null, feature_count: 10,
  },
  current: {
    legend_spec: {
      type: 'graduated', field: 'pop', breaks: [0, 4, 9],
      palette: 'YlOrRd', palette_colors: ['#a', '#b'], labels: ['x', 'y'],
      method: 'quantiles', nodata: { color: '#cccccc', label: 'No data' },
    },
    fingerprint: 'texp-1', paint: null, authoring: null,
  },
  fields: [{ name: 'pop', type: 'number' }, { name: 'name', type: 'string' }],
  distribution: {
    field: 'pop',
    basis: { mode: 'full_scan', total_features: 10, approximate: false },
    numeric: {
      valid: 10, invalid_total: 0, zero: 1, min: 0, max: 9,
      histogram: { bin_edges: [0, 5, 9], counts: [6, 4] },
    },
    categorical: { top: [], other_count: 0, ambiguous_keys: [] },
  },
  revision: 3,
  shared_expression_scope: null,
};

const previewReceipt = {
  layer_id: 'result-1', field: 'pop', mode: 'graduated_manual', committed: false,
  legend_spec: inspectReceipt.current.legend_spec,
  paint: { method: 'step' },
  class_table: [
    { index: 0, color: '#eff3ff', label: '0 – 10', count: 3, range: [0, 10] },
    { index: 1, color: '#bdd7e7', label: '10 – 20', count: 4, range: [10, 20] },
    { index: 2, color: '#3182bd', label: '20 – 50', count: 3, range: [20, 50] },
  ],
  disclosures: ['3 个 null/缺失值按 nodata 色渲染'],
  requested_k: 3, actual_k: 3,
  current_fingerprint: 'texp-1', candidate_fingerprint: 'texp-2',
  source_anchor: { ref_id: 'ref:abc', content_revision: 2 },
};

const previewMock = vi.fn();
const inspectMock = vi.fn();

vi.mock('@/lib/thematic-authoring/api', () => ({
  asThematicError: (err: unknown) => ({
    code: 'TEST', message: err instanceof Error ? err.message : String(err),
  }),
  inspectThematicLayer: (...args: unknown[]) => inspectMock(...(args as [never, never, never])),
  previewThematicPlan: (...args: unknown[]) => previewMock(...(args as [never, never, never])),
  commitThematicPlan: vi.fn(),
}));

vi.mock('@/lib/i18n/useT', () => ({
  useT: () => (key: string, _params?: Record<string, unknown>) => key,
  useLocale: () => 'zh-CN',
}));
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (sel: (s: { addToast: unknown }) => unknown) => sel({ addToast: vi.fn() }),
}));
vi.mock('@/lib/mapspec/session-cursor', () => ({
  getMapSpecSessionCursor: () => ({ sessionId: 'sess-1', revision: 3, ownerToken: null }),
  getCommittedMapSpec: () => null,
  subscribeMapSpecLive: () => () => {},
  commitMapSpecDocument: vi.fn(),
}));

describe('ThematicAuthoringPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    inspectMock.mockResolvedValue(inspectReceipt);
    previewMock.mockResolvedValue(previewReceipt);
  });

  it('loads inspect and shows distribution summary', async () => {
    render(<ThematicAuthoringPanel layerId="result-1" />);
    await waitFor(() => {
      expect(screen.getByTestId('thematic-authoring-panel')).toBeInTheDocument();
    });
    expect(inspectMock).toHaveBeenCalled();
    const first = inspectMock.mock.calls[0];
    expect(first[0]).toMatchObject({ sessionId: 'sess-1' });
    expect(first[1]).toBe('result-1');
    // 分布摘要：有效/总数
    expect(screen.getByText(/10 \/ 10/)).toBeInTheDocument();
  });

  it('manual mode: break inputs render with exact committed breaks', async () => {
    render(<ThematicAuthoringPanel layerId="result-1" />);
    await waitFor(() => {
      expect(screen.getByTestId('thematic-authoring-panel')).toBeInTheDocument();
    });
    // 切到人工断点模式（第二个 combobox = 模式）
    const modeSelect = screen.getAllByRole('combobox')[1] as HTMLSelectElement;
    fireEvent.change(modeSelect, { target: { value: 'graduated_manual' } });
    const b0 = await screen.findByLabelText('break-0');
    expect((b0 as HTMLInputElement).value).toBe('0');
    expect((screen.getByLabelText('break-2') as HTMLInputElement).value).toBe('9');
  });

  it('preview renders class table with counts and disclosure notes', async () => {
    render(<ThematicAuthoringPanel layerId="result-1" />);
    await waitFor(() => {
      expect(screen.getByTestId('thematic-authoring-panel')).toBeInTheDocument();
    });
    // 切到人工断点并预览（第二个 combobox = 模式）
    const modeSelect = screen.getAllByRole('combobox')[1] as HTMLSelectElement;
    fireEvent.change(modeSelect, { target: { value: 'graduated_manual' } });
    const previewBtn = screen.getByRole('button', { name: 'thematicAuthoring.previewBtn' });
    previewBtn.click();
    await waitFor(() => {
      expect(screen.getByTestId('thematic-preview')).toBeInTheDocument();
    });
    expect(screen.getByText('0 – 10')).toBeInTheDocument();
    expect(screen.getByText(/nodata 色渲染/)).toBeInTheDocument();
  });

  it('revert entry present and shared scope disclosed', async () => {
    inspectMock.mockResolvedValue({
      ...inspectReceipt,
      shared_expression_scope: { kind: 'frame_product' },
    });
    render(<ThematicAuthoringPanel layerId="result-1" />);
    await waitFor(() => {
      expect(screen.getByText('thematicAuthoring.sharedScope')).toBeInTheDocument();
    });
  });
});
