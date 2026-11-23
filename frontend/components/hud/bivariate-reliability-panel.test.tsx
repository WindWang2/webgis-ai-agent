/**
 * G41 面板 smoke：inspect 强版本锚、canonical 矩阵与精确计数、设置改变
 * 使旧预览失效、逐字段错误定位。API 层 mock（业务裁决真相在服务端单测）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { BivariateReliabilityPanel } from './bivariate-reliability-panel';

const inspectReceipt = {
  layer_id: 'L1',
  layer_type: 'fill',
  source: {
    source_id: 's1', base_ref: 'ref:abc', content_revision: 3,
    content_fingerprint: 'g41f-xyz', feature_count: 9,
    snapshot_guarantee: 'monotonic generation + lock',
  },
  source_anchor: {
    ref_id: 'ref:abc', content_revision: 3, content_fingerprint: 'g41f-xyz',
  },
  current: {
    legend_type: 'graduated', fingerprint: 'g41e-1',
    authoring: null, source_has_biv_class: false,
  },
  fields: [
    { name: 'income', numeric_count: 9, nonnumeric_count: 0, type: 'number', kind_counts: { number: 9 } },
    { name: 'rate', numeric_count: 9, nonnumeric_count: 0, type: 'number', kind_counts: { number: 9 } },
    { name: 'est', numeric_count: 9, nonnumeric_count: 0, type: 'number', kind_counts: { number: 9 } },
  ],
  fields_truncated: false,
  supported: {
    layer_type: 'fill', bivariate_matrix: true, reliability_opacity: true,
    reliability_stroke_width: false, note: 'test',
  },
  measure_evidence_contract: {
    accepted: ['standard_error'], requires: 'same source row',
    not_accepted: ['sample_count'], disclosure: 'no fabricated confidence',
  },
  revision: 1,
  shared_expression_scope: null,
  algorithm_version: 'g41-authoring.v1',
};

const cells = [6, 7, 8, 3, 4, 5, 0, 1, 2].map((classIndex, i) => ({
  screen_row: Math.floor(i / 3), screen_col: i % 3,
  row: 2 - Math.floor(i / 3), col: i % 3,
  class_index: classIndex, color: `#${(i + 1).toString(16).padStart(6, '0')}`,
  count: i + 1, empty: i === 1,
  row_interval: { label: '[0, 10) %' }, col_interval: { label: '[0, 10) 人' },
}));

const previewReceipt = {
  success: true, layer_id: 'L1', mode: 'bivariate_matrix', committed: false,
  preview_only: true, algorithm_version: 'g41-authoring.v1',
  source_anchor: inspectReceipt.source_anchor,
  plan_digest: 'abcd', candidate_digest: 'g41c-1234567890',
  current_fingerprint: 'g41e-1', candidate_fingerprint: 'g41e-2',
  legend: {
    type: 'bivariate', n: 3, boundary_rule: 'upper_inclusive', row_major: true,
    class_field: '__g41_pair_class', colors: cells.map((c) => c.color),
    cells, joint: [[2, 0, 0], [0, 2, 1], [0, 0, 4]],
    axes: {
      row: { field: 'rate', label: 'rate', unit: '%', method: 'equal_interval', arrow: 'up', actual_classes: 3, intervals: [] },
      col: { field: 'income', label: 'income', unit: '人', method: 'quantiles', arrow: 'right', actual_classes: 3, intervals: [] },
    },
    counts: { valid_pairs: 9 }, missing: { label: '无数据', count: 0, policy: 'independent' },
    source: { ref_id: 'ref:abc', content_revision: 3 }, approximate: false,
  },
  stats_basis: { valid_pairs: 9, approximate: false },
  summary: { valid_pairs: 9, requested_classes: 3, actual_classes_a: 3, actual_classes_b: 3 },
  disclosures: ['联合计数来自完整冻结总体', '散点是有界样本'],
  derived: { fields: ['__g41_pair_class'], ref: null, reversible: true,
             source_written_back: false, source_has_biv_class: false },
  revision: 1,
};

const inspectMock = vi.fn();
const previewMock = vi.fn();
const commitMock = vi.fn();

vi.mock('@/lib/bivariate-reliability/api', () => ({
  asG41Error: (err: unknown) => ({
    code: 'TEST_ERROR', message: err instanceof Error ? err.message : String(err),
    field: 'axis_a.breaks', index: 1, correction_hint: 'fix it',
  }),
  inspectG41Layer: (...args: unknown[]) => inspectMock(...(args as [never, never])),
  previewG41Plan: (...args: unknown[]) => previewMock(...(args as [never, never, never])),
  commitG41Plan: (...args: unknown[]) => commitMock(...(args as [never, never, never, never, never, never])),
}));
vi.mock('@/lib/i18n/useT', () => ({
  useT: () => (key: string, _p?: Record<string, unknown>) => key,
  useLocale: () => 'zh-CN',
}));
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (sel: (s: { addToast: unknown }) => unknown) => sel({ addToast: vi.fn() }),
}));
vi.mock('@/lib/mapspec/session-cursor', () => ({
  getMapSpecSessionCursor: () => ({ sessionId: 'sess-1', revision: 1, ownerToken: null }),
  getCommittedMapSpec: () => null,
  subscribeMapSpecLive: () => () => {},
  commitMapSpecDocument: vi.fn(),
}));

describe('BivariateReliabilityPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    inspectMock.mockResolvedValue(inspectReceipt);
    previewMock.mockResolvedValue(previewReceipt);
    commitMock.mockResolvedValue({ ...previewReceipt, committed: true });
  });

  it('loads the strong source anchor and field profile', async () => {
    render(<BivariateReliabilityPanel layerId="L1" />);
    await waitFor(() => {
      expect(screen.getByTestId('g41-authoring-panel')).toBeInTheDocument();
    });
    expect(screen.getByText(/rev 3/)).toBeInTheDocument();
    expect(screen.getByTestId('g41-bivariate-form')).toBeInTheDocument();
    expect(screen.getByLabelText('bivariateReliability.axisA')).toBeInTheDocument();
  });

  it('renders canonical matrix with exact joint counts and per-cell tooltips', async () => {
    render(<BivariateReliabilityPanel layerId="L1" />);
    await waitFor(() => expect(screen.getByTestId('g41-bivariate-form')).toBeInTheDocument());
    fireEvent.click(screen.getByText('bivariateReliability.previewBtn'));
    await waitFor(() => expect(screen.getByTestId('g41-matrix')).toBeInTheDocument());
    expect(screen.getByTestId('g41-cell-6')).toHaveAttribute('title',
      expect.stringContaining('n=1'));
    expect(screen.getByTestId('g41-preview').textContent)
      .toContain('bivariateReliability.pairs');
    expect(screen.getByTestId('g41-cell-6').textContent).toBe('1');
  });

  it('invalidates the preview when any setting changes (B27)', async () => {
    render(<BivariateReliabilityPanel layerId="L1" />);
    await waitFor(() => expect(screen.getByTestId('g41-bivariate-form')).toBeInTheDocument());
    fireEvent.click(screen.getByText('bivariateReliability.previewBtn'));
    await waitFor(() => expect(screen.getByTestId('g41-preview')).toBeInTheDocument());
    const apply = screen.getByText('bivariateReliability.applyBtn').closest('button');
    expect(apply).not.toBeDisabled();
    fireEvent.change(screen.getByLabelText('bivariateReliability.unitA'),
      { target: { value: '万元' } });
    await waitFor(() => expect(screen.getByTestId('g41-stale-preview')).toBeInTheDocument());
    expect(screen.getByText('bivariateReliability.applyBtn').closest('button')).toBeDisabled();
    expect(commitMock).not.toHaveBeenCalled();
  });

  it('shows field/index-located validation errors', async () => {
    previewMock.mockRejectedValueOnce(new Error('bad breaks'));
    render(<BivariateReliabilityPanel layerId="L1" />);
    await waitFor(() => expect(screen.getByTestId('g41-bivariate-form')).toBeInTheDocument());
    fireEvent.click(screen.getByText('bivariateReliability.previewBtn'));
    await waitFor(() => expect(screen.getByTestId('g41-error')).toBeInTheDocument());
    const errorText = screen.getByTestId('g41-error').textContent ?? '';
    expect(errorText).toContain('@axis_a.breaks');
    expect(errorText).toContain('[1]');
    expect(errorText).toContain('fix it');
  });

  it('releases busy after a successful apply so authoring stays usable', async () => {
    // 复审 P1：runCommit 里 `await reload()` 会推进 epoch，若 finally 仍按
    // epoch 相等才释放 busy，成功后按钮会永久禁用（只能重挂面板恢复）。
    render(<BivariateReliabilityPanel layerId="L1" />);
    await waitFor(() => expect(screen.getByTestId('g41-bivariate-form')).toBeInTheDocument());
    fireEvent.click(screen.getByText('bivariateReliability.previewBtn'));
    await waitFor(() => expect(screen.getByTestId('g41-preview')).toBeInTheDocument());
    fireEvent.click(screen.getByText('bivariateReliability.applyBtn'));
    await waitFor(() => expect(commitMock).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      expect(screen.getByText('bivariateReliability.previewBtn').closest('button'))
        .not.toBeDisabled();
    });
    // 提交后旧的候选已消费：Apply 必须重新变为不可用，直到再次预览
    expect(commitMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText('bivariateReliability.previewBtn'));
    await waitFor(() => {
      expect(screen.getByText('bivariateReliability.applyBtn').closest('button'))
        .not.toBeDisabled();
    });
  });

  it('releases busy when the preview is cancelled mid-flight (reqGen)', async () => {
    // 复审 P1：runPreview 的 finally 原先按 epoch/gen 相等才释放 busy，
    // Cancel（reqGenRef+1）会使在飞快照的 busy 永为真，预览/应用按钮
    // 永久禁用，只能重挂面板恢复。
    let resolvePreview: (v: typeof previewReceipt) => void = () => {};
    previewMock.mockImplementation(
      () => new Promise((res) => { resolvePreview = res; }));
    render(<BivariateReliabilityPanel layerId="L1" />);
    await waitFor(() => expect(screen.getByTestId('g41-bivariate-form')).toBeInTheDocument());
    fireEvent.click(screen.getByText('bivariateReliability.previewBtn'));
    const previewBtn = screen.getByText('bivariateReliability.previewBtn').closest('button');
    await waitFor(() => expect(previewBtn).toBeDisabled());
    fireEvent.click(screen.getByText('bivariateReliability.cancelBtn'));
    resolvePreview(previewReceipt);
    await waitFor(() => expect(previewBtn).not.toBeDisabled());
    // 被作废的预览不得上屏，也不得留下可提交的候选
    expect(screen.queryByTestId('g41-preview')).not.toBeInTheDocument();
    expect(screen.getByText('bivariateReliability.applyBtn').closest('button')).toBeDisabled();
  });

  it('releases busy when the layer changes mid-flight (epoch)', async () => {
    // 复审 P1：切层/liveRevision 变更推进 epochRef，在飞快照的 finally
    // 若仍按 epoch 相等才释放 busy，新层上面板按钮同样永久禁用。
    let resolvePreview: (v: typeof previewReceipt) => void = () => {};
    previewMock.mockImplementation(
      () => new Promise((res) => { resolvePreview = res; }));
    const { rerender } = render(<BivariateReliabilityPanel layerId="L1" />);
    await waitFor(() => expect(screen.getByTestId('g41-bivariate-form')).toBeInTheDocument());
    fireEvent.click(screen.getByText('bivariateReliability.previewBtn'));
    const previewBtn = screen.getByText('bivariateReliability.previewBtn').closest('button');
    await waitFor(() => expect(previewBtn).toBeDisabled());
    rerender(<BivariateReliabilityPanel layerId="L2" />);
    resolvePreview(previewReceipt);
    await waitFor(() => expect(previewBtn).not.toBeDisabled());
    // 迟到响应不得把旧层的候选上屏
    expect(screen.queryByTestId('g41-preview')).not.toBeInTheDocument();
  });

  it('offers the reliability mode with measure and visual controls', async () => {
    render(<BivariateReliabilityPanel layerId="L1" />);
    await waitFor(() => expect(screen.getByTestId('g41-bivariate-form')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('bivariateReliability.mode'),
      { target: { value: 'reliability_dual_channel' } });
    await waitFor(() => expect(screen.getByTestId('g41-reliability-form')).toBeInTheDocument());
    expect(screen.getByLabelText('bivariateReliability.measureKind')).toBeInTheDocument();
    expect(screen.getByText(/no fabricated confidence/)).toBeInTheDocument();
  });
});
