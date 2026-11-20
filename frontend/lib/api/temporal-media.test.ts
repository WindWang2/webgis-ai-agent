/** G35 API 客户端：路径/方法/ownerToken/关闭词表请求形状与响应透传。 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('./transport', () => ({ apiFetch: mocks.apiFetch }));

import {
  cancelMediaJob, getMediaCapabilities, getMediaJobResult, getMediaJobStatus,
  listMediaJobs, previewMediaPlan, submitMediaJob,
} from './temporal-media';

const request = {
  session_id: 'sess-g35',
  format: 'apng' as const,
  timing_policy: 'equal' as const,
  dwell_ms: 1000,
  width: 640,
  height: 360,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('temporal-media API client', () => {
  it('capabilities 走 GET 且带 session_id 与 ownerToken', async () => {
    mocks.apiFetch.mockResolvedValue({ success: true, capabilities: {}, binding: {} });
    await getMediaCapabilities('sess g35', 'tok');
    expect(mocks.apiFetch).toHaveBeenCalledWith(
      '/api/v1/temporal-media/capabilities?session_id=sess%20g35',
      expect.objectContaining({ ownerToken: 'tok' }),
    );
  });

  it('preview / submit 共用同一请求面，submit 携带短 plan_ref', async () => {
    mocks.apiFetch.mockResolvedValueOnce({ plan_hash: 'h'.repeat(64), plan: {} });
    await previewMediaPlan(request, 'tok');
    expect(mocks.apiFetch).toHaveBeenCalledWith(
      '/api/v1/temporal-media/plan',
      expect.objectContaining({ method: 'POST', ownerToken: 'tok', body: request }),
    );
    mocks.apiFetch.mockResolvedValueOnce({ success: true, status: 'pending', job_id: 'g35-1' });
    await submitMediaJob({ ...request, plan_ref: 'abcdef0123456789' }, 'tok');
    expect(mocks.apiFetch.mock.calls[1][1]).toMatchObject({
      method: 'POST',
      body: expect.objectContaining({ plan_ref: 'abcdef0123456789' }),
    });
  });

  it('status / result / cancel / list 的路径与 HTTP 方法正确', async () => {
    mocks.apiFetch.mockResolvedValue({ success: true, status: 'pending' });
    await getMediaJobStatus('sess-g35', 'g35-1');
    await getMediaJobResult('sess-g35', 'g35-1');
    await cancelMediaJob('sess-g35', 'g35-1');
    await listMediaJobs('sess-g35');
    const calls = mocks.apiFetch.mock.calls;
    expect(calls[0][0]).toContain('/jobs/g35-1?session_id=sess-g35');
    expect(calls[1][0]).toContain('/jobs/g35-1/result?session_id=sess-g35');
    expect(calls[2][0]).toContain('/jobs/g35-1?session_id=sess-g35');
    expect(calls[2][1]).toMatchObject({ method: 'DELETE' });
    expect(calls[3][0]).toBe('/api/v1/temporal-media/jobs?session_id=sess-g35');
  });

  it('不向后端发送路径/URL/shell 参数（关闭词表请求形状）', async () => {
    mocks.apiFetch.mockResolvedValue({ plan_hash: 'x', plan: {} });
    await previewMediaPlan({ ...request, frame_ids: ['f-2019', 'f-2021'] });
    const body = mocks.apiFetch.mock.calls[0][1].body;
    expect(Object.keys(body).sort()).toEqual(
      ['dwell_ms', 'format', 'frame_ids', 'height', 'session_id',
        'timing_policy', 'width'].sort(),
    );
    expect(JSON.stringify(body)).not.toContain('/tmp');
  });
});
