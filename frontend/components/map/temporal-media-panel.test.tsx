/** G35 媒体面板：真实观测/缺期披露、pending 不冒充完成、下载与恢复。 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const api = vi.hoisted(() => ({
  getMediaCapabilities: vi.fn(),
  previewMediaPlan: vi.fn(),
  submitMediaJob: vi.fn(),
  getMediaJobStatus: vi.fn(),
  cancelMediaJob: vi.fn(),
  getMediaJobResult: vi.fn(),
  listMediaJobs: vi.fn(),
  downloadWithAuth: vi.fn(),
}));

vi.mock('@/lib/api/temporal-media', () => ({
  getMediaCapabilities: api.getMediaCapabilities,
  previewMediaPlan: api.previewMediaPlan,
  submitMediaJob: api.submitMediaJob,
  getMediaJobStatus: api.getMediaJobStatus,
  cancelMediaJob: api.cancelMediaJob,
  getMediaJobResult: api.getMediaJobResult,
  listMediaJobs: api.listMediaJobs,
}));
vi.mock('@/lib/api/authenticated-download', () => ({
  downloadWithAuth: api.downloadWithAuth,
}));
vi.mock('@/lib/store/session-identity', () => ({
  getSessionIdentity: () => ({ sessionId: 'sess-g35', ownerToken: 'tok' }),
}));

import { TemporalMediaPanel } from './temporal-media-panel';

const ALL_OBS = [
  { id: 'f-2019', label: '2019', start: '2019-01-01T00:00:00Z', end: '2020-01-01T00:00:00Z' },
  { id: 'f-2021', label: '2021', start: '2021-01-01T00:00:00Z', end: '2022-01-01T00:00:00Z' },
  { id: 'f-2024', label: '2024', start: '2024-01-01T00:00:00Z', end: '2025-01-01T00:00:00Z' },
];

const preview = {
  plan_hash: 'a'.repeat(64),
  plan_ref: 'abcdef0123456789',
  all_observations: ALL_OBS,
  plan: {
    observations: [
      { id: 'f-2019', label: '2019', start: '2019-01-01T00:00:00Z', end: '2020-01-01T00:00:00Z' },
      { id: 'f-2021', label: '2021', start: '2021-01-01T00:00:00Z', end: '2022-01-01T00:00:00Z' },
      { id: 'f-2024', label: '2024', start: '2024-01-01T00:00:00Z', end: '2025-01-01T00:00:00Z' },
    ],
    missing_periods: [{ id: 'g1', label: '2020', start: '', end: '' }],
    selection: { omitted_observations: 0, actual_span_ms: 189345600000, selection: 'all' },
    display: {
      segments: [
        { index: 0, kind: 'observation', observation_id: 'f-2019',
          observation_label: '2019', time_label: '2019', identity: 'real_observation',
          missing_period: false, held_from: null, duration_ms: 1000,
          encoded_repeat: 1, encoded_durations_ms: [1000] },
      ],
      timing: { policy: 'equal', total_display_ms: 3000, rule: 'd_i = dwell_ms',
        quantization_error_ms: 0 },
      order: 'chronological', missing_display: 'skip',
      observation_count: 3, missing_period_count: 3, encoded_frame_count: 3,
    },
    canvas: { width: 1280, height: 720 },
    format: 'apng',
    partial: { is_partial: false, omitted_layers: [] },
    disclosures: ['missing_periods=3', 'fixed_viewport_and_style'],
    resource_estimate: { unique_digital_frames: 3, encoded_frames: 3,
      raw_pixel_bytes: 11059200, estimated_temp_bytes: 22118400, total_display_ms: 3000 },
  },
};

const completedResult = {
  success: true, status: 'completed', job_id: 'g35-1',
  artifacts: {
    animation: { filename: 'a.apng', bytes: 1234, media_type: 'image/apng',
      checksum: 'sha256:x', url: '/api/v1/export/download/a.apng?exp=1&sig=s' },
    evidence: { filename: 'e.zip', bytes: 2345, media_type: 'application/zip',
      checksum: 'sha256:y', url: '/api/v1/export/download/e.zip?exp=1&sig=s' },
    manifest: { filename: 'm.json', bytes: 345, media_type: 'application/json',
      checksum: 'sha256:z', url: '/api/v1/export/download/m.json?exp=1&sig=s' },
  },
  verification: { passed: true, decoded_frames: 3, checks_count: 40 },
  manifest_summary: { observation_count: 3, missing_periods: 3, segments: 3,
    total_display_ms: 3000, disclosures: [] },
};

beforeEach(() => {
  vi.clearAllMocks();
  api.getMediaCapabilities.mockResolvedValue({
    success: true,
    capabilities: {
      formats: [{ name: 'apng', available: true, core: true },
        { name: 'gif', available: true, core: false },
        { name: 'mp4', available: false, core: false, reason: 'not deployed' }],
      renderer: { available: true, engine: 'mapspec-to-svg+rsvg-convert' },
      limits: { max_observations: 120, max_encoded_frames: 240,
        max_total_display_ms: 120000, width: [320, 1920], height: [240, 1080] },
    },
    binding: { available: true, observations: 3 },
  });
  api.previewMediaPlan.mockResolvedValue(preview);
  api.listMediaJobs.mockResolvedValue({ success: true, jobs: [] });
  api.submitMediaJob.mockResolvedValue({
    success: true, status: 'pending', job_id: 'g35-1', plan_hash: preview.plan_hash,
  });
  api.getMediaJobStatus.mockResolvedValue({
    success: true, status: 'pending', phase: 'queued', progress: null,
    message: 'pending', completed: false,
  });
});

describe('TemporalMediaPanel', () => {
  it('展示真实观测/缺期/时间解释与格式能力（视频未部署不开放）', async () => {
    render(<TemporalMediaPanel />);
    await waitFor(() => expect(screen.getByTestId('temporal-media-preview')).toBeTruthy());
    expect(screen.getByText('2019')).toBeTruthy();
    expect(screen.getByText('2021')).toBeTruthy();
    expect(screen.getByText('2024')).toBeTruthy();
    expect(screen.getByTestId('temporal-media-preview').textContent).toContain('缺期');
    const format = screen.getByTestId('temporal-media-format') as HTMLSelectElement;
    const options = Array.from(format.options).map((o) => o.value);
    expect(options).toContain('apng');
    expect(options).toContain('gif');
    expect(options).not.toContain('mp4');
  });

  it('提交返回 pending 时不显示成果下载；完成后才显示', async () => {
    render(<TemporalMediaPanel />);
    await waitFor(() => expect(screen.getByTestId('temporal-media-submit')).toBeTruthy());
    fireEvent.click(screen.getByTestId('temporal-media-submit'));
    await waitFor(() => expect(screen.getByTestId('temporal-media-status')).toBeTruthy());
    expect(screen.getByTestId('temporal-media-status').textContent).toContain('pending');
    expect(screen.queryByTestId('temporal-media-result')).toBeNull();

    api.getMediaJobStatus.mockResolvedValue({
      success: true, status: 'completed', phase: 'completed', progress: 100,
      message: 'ok', completed: true, verification: { passed: true },
    });
    api.getMediaJobResult.mockResolvedValue(completedResult);
    fireEvent.click(screen.getByTestId('temporal-media-refresh'));
    await waitFor(() => expect(screen.getByTestId('temporal-media-result')).toBeTruthy());
  });

  it('下载按钮真实调用授权下载（非伪造 blob）', async () => {
    api.listMediaJobs.mockResolvedValue({ success: true, jobs: [
      { job_id: 'g35-1', status: 'completed', phase: 'completed', progress: 100,
        plan_hash: preview.plan_hash, message: 'ok' },
    ] });
    api.getMediaJobStatus.mockResolvedValue({
      success: true, status: 'completed', phase: 'completed', progress: 100,
      message: 'ok', completed: true,
    });
    api.getMediaJobResult.mockResolvedValue(completedResult);
    render(<TemporalMediaPanel />);
    await waitFor(() => expect(screen.getByTestId('temporal-media-result')).toBeTruthy());
    fireEvent.click(screen.getByTestId('temporal-media-download-animation'));
    await waitFor(() => expect(api.downloadWithAuth).toHaveBeenCalledWith(
      completedResult.artifacts.animation.url,
      expect.objectContaining({ filename: 'a.apng', ownerToken: 'tok' }),
    ));
    fireEvent.click(screen.getByTestId('temporal-media-download-evidence'));
    await waitFor(() => expect(api.downloadWithAuth).toHaveBeenCalledTimes(2));
  });

  it('修改设置后直接提交会先重新预览并使用新 plan_ref（不是旧计划 409）', async () => {
    const fresh = { ...preview, plan_hash: 'b'.repeat(64),
      plan_ref: 'bbbbbbbbbbbbbbbb' };
    api.previewMediaPlan.mockResolvedValueOnce(preview)
      .mockResolvedValueOnce(fresh);
    render(<TemporalMediaPanel />);
    await waitFor(() => expect(screen.getByTestId('temporal-media-preview')).toBeTruthy());
    fireEvent.change(screen.getByTestId('temporal-media-dwell'),
      { target: { value: '2000' } });
    fireEvent.click(screen.getByTestId('temporal-media-submit'));
    await waitFor(() => expect(api.previewMediaPlan).toHaveBeenCalledTimes(2));
    expect(api.submitMediaJob).toHaveBeenCalledWith(
      expect.objectContaining({ dwell_ms: 2000, plan_ref: 'bbbbbbbbbbbbbbbb' }),
      'tok');
  });

  it('勾选子集→预览→提交保留子集（不会静默交付全部观测）', async () => {
    const subset = {
      ...preview,
      plan_ref: 'cccccccccccccccc',
      plan: {
        ...preview.plan,
        observations: [ALL_OBS[0], ALL_OBS[2]],
      },
    };
    api.previewMediaPlan.mockResolvedValueOnce(preview)
      .mockResolvedValueOnce(subset);
    render(<TemporalMediaPanel />);
    await waitFor(() => expect(screen.getByTestId('temporal-media-preview')).toBeTruthy());
    // 取消勾选 2021
    fireEvent.click(screen.getAllByRole('checkbox')[1]);
    // 显式点「预览计划」，子集必须继续留在勾选面
    fireEvent.click(screen.getByText('预览计划'));
    await waitFor(() => expect(api.previewMediaPlan).toHaveBeenCalledTimes(2));
    expect(screen.getAllByRole('checkbox')).toHaveLength(3);
    fireEvent.click(screen.getByTestId('temporal-media-submit'));
    await waitFor(() => expect(api.submitMediaJob).toHaveBeenCalled());
    // 提交不得再次重预览（否则说明 selected 被重置并与 ref 不一致）
    expect(api.previewMediaPlan).toHaveBeenCalledTimes(2);
    expect(api.submitMediaJob).toHaveBeenCalledWith(
      expect.objectContaining({
        frame_ids: ['f-2019', 'f-2024'],
        plan_ref: 'cccccccccccccccc',
      }), 'tok');
  });

  it('恢复时按 created_at 取最近任务而不是 job_id 字典序', async () => {
    api.listMediaJobs.mockResolvedValue({ success: true, jobs: [
      { job_id: 'g35-zzz', status: 'completed', phase: 'completed', progress: 100,
        plan_hash: 'a'.repeat(64), message: 'old', created_at: 100 },
      { job_id: 'g35-aaa', status: 'completed', phase: 'completed', progress: 100,
        plan_hash: 'a'.repeat(64), message: 'new', created_at: 900 },
    ] });
    api.getMediaJobStatus.mockResolvedValue({
      success: true, status: 'completed', phase: 'completed', progress: 100,
      message: 'new', completed: true,
    });
    api.getMediaJobResult.mockResolvedValue(completedResult);
    render(<TemporalMediaPanel />);
    await waitFor(() => expect(api.getMediaJobResult).toHaveBeenCalled());
    expect(api.getMediaJobResult.mock.calls[0][1]).toBe('g35-aaa');
  });

  it('会话未绑定 temporal 时如实展示原因，不伪造帧列表', async () => {
    api.previewMediaPlan.mockRejectedValue(new Error('NO_TEMPORAL_BINDING'));
    render(<TemporalMediaPanel />);
    await waitFor(() => expect(screen.getByTestId('temporal-media-error')).toBeTruthy());
    expect(screen.queryByTestId('temporal-media-preview')).toBeNull();
  });
});
