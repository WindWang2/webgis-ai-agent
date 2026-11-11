/**
 * 数据交付对话框接线测试（G18 WP10）。
 *
 * 覆盖：源展示、格式选择、预览（损失/阻断）、损失确认提交、回执渲染。
 * API 客户端打桩（mock 只替代网络边界；被测的是组件接线与诚实性呈现）。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DataDeliveryDialog } from './delivery-dialog';
import type { DeliveryPreview } from '@/lib/api/data-delivery';

const previewMock = vi.hoisted(() => vi.fn());
const submitMock = vi.hoisted(() => vi.fn());
const listMock = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api/data-delivery', () => ({
  previewDataDelivery: previewMock,
  submitDataDelivery: submitMock,
  listDeliveryJobs: listMock,
  getDeliveryJob: vi.fn(),
  fetchDeliveryCapabilities: vi.fn(),
}));

const source = {
  kind: 'session_ref' as const,
  session_id: 'sess-1',
  ref: 'ref:data-fabric-abc',
};

function previewStub(overrides: Partial<DeliveryPreview> = {}): DeliveryPreview {
  return {
    spec_fingerprint: 'fp-1',
    source: {
      kind: 'session_ref',
      label: 'ref:data-fabric-abc',
      content_fingerprint: 'sha',
      row_count: 3,
      is_table: false,
      crs: 'EPSG:4326',
      crs_known: true,
      fields: [{ name: 'name', inferred_type: 'str' }],
    },
    requested_format: {
      format: 'geojson',
      available: true,
      constrained: false,
      reason: null,
      notes: [],
      extensions: ['.geojson'],
    },
    formats: [],
    estimated_rows: 3,
    estimated_bytes: 2700,
    losses: [
      { code: 'nested_value_json_encoded', message: '嵌套字段按 JSON 字符串编码', requires_ack: false },
    ],
    blocking_issues: [],
    required_acknowledgments: [],
    ...overrides,
  };
}

beforeEach(() => {
  previewMock.mockReset();
  submitMock.mockReset();
  listMock.mockReset();
  listMock.mockResolvedValue([]);
});

describe('DataDeliveryDialog', () => {
  it('renders source label and preview losses honestly', async () => {
    previewMock.mockResolvedValue(previewStub());
    render(
      <DataDeliveryDialog open onClose={() => {}} source={source} sourceLabel="首都设施" />,
    );
    expect(screen.getByText('首都设施')).toBeTruthy();
    await userEvent.click(screen.getByText('预览交付'));
    await waitFor(() => expect(screen.getByText(/嵌套字段按 JSON 字符串编码/)).toBeTruthy());
    // 无阻断项 → 提交按钮出现
    expect(screen.getByText('确认损失并交付')).toBeTruthy();
  });

  it('shows blocking issues and hides submit when source lacks CRS', async () => {
    previewMock.mockResolvedValue(previewStub({
      blocking_issues: [
        { code: 'MISSING_SOURCE_CRS', message: '源数据没有 CRS 标注，拒绝默认按 EPSG:4326 解释', requires_ack: false },
      ],
    }));
    render(<DataDeliveryDialog open onClose={() => {}} source={source} />);
    await userEvent.click(screen.getByText('预览交付'));
    await waitFor(() =>
      expect(screen.getByText(/拒绝默认按 EPSG:4326 解释/)).toBeTruthy());
    expect(screen.queryByText('确认损失并交付')).toBeNull();
  });

  it('submits with the preview required acknowledgments', async () => {
    previewMock.mockResolvedValue(previewStub({
      required_acknowledgments: ['crs_label_unknown'],
    }));
    submitMock.mockResolvedValue({ job_id: '7', status: 'running', idempotent_reuse: false, message: 'ok' });
    const { listDeliveryJobs } = await import('@/lib/api/data-delivery');
    (listDeliveryJobs as unknown as { mockResolvedValue: (v: unknown) => void })
      .mockResolvedValue([]);
    render(<DataDeliveryDialog open onClose={() => {}} source={source} />);
    await userEvent.click(screen.getByText('预览交付'));
    await waitFor(() => expect(screen.getByText('确认损失并交付')).toBeTruthy());
    await userEvent.click(screen.getByText('确认损失并交付'));
    await waitFor(() =>
      expect(submitMock).toHaveBeenCalledWith(
        expect.anything(),
        ['crs_label_unknown'],
        undefined,
      ));
  });

  it('recovers history list from server state', async () => {
    previewMock.mockResolvedValue(previewStub());
    listMock.mockResolvedValue([
      {
        job_id: '12',
        status: 'completed',
        progress: 100,
        format: 'geojson',
        filename: '首都设施',
        receipt: {
          delivery_id: 'd1', job_id: '12', format: 'geojson', status: 'completed',
          filename: '首都设施', members: [], row_count: 3, bytes_total: 900,
          crs: { transformed: false }, losses_applied: [], scope: {},
          source: {}, writer_versions: {},
        },
        download_url: '/api/v1/data-delivery/jobs/12/download',
      },
    ]);
    render(<DataDeliveryDialog open onClose={() => {}} source={source} />);
    await waitFor(() => expect(screen.getByText('历史交付')).toBeTruthy());
    expect(screen.getByText(/首都设施 · completed/)).toBeTruthy();
  });
});
