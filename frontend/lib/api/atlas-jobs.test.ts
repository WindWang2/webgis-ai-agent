/**
 * atlas-jobs API 客户端测试：请求形状/响应透传/错误标签（真实 apiFetch
 * 面以 transport mock 隔离 —— 客户端自身的契约是被测对象）。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

const apiFetch = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('./transport', () => ({ apiFetch: apiFetch.apiFetch }));

import { getAtlasJob, submitAtlasJob } from './atlas-jobs';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('submitAtlasJob', () => {
  it('POST /api/v1/atlas/jobs，body 与 ownerToken 透传', async () => {
    apiFetch.apiFetch.mockResolvedValue({
      success: true,
      job_id: '7',
      status: 'analysis_task_started',
      idempotent_reuse: false,
      message: 'ok',
      plan: { page_count: 13, cover: true, first_page_id: 'cover', last_page_id: 'cat_龙', degradations: [] },
      poll_url: '/api/v1/tasks/jobs/7',
    });
    const resp = await submitAtlasJob(
      {
        session_id: 'sess-1',
        atlas: { driver: 'category', categoryProperty: 'district', includeCover: true },
        classification_scope: 'shared',
        target_dpi: 300,
      },
      { ownerToken: 'tok' },
    );
    expect(apiFetch.apiFetch).toHaveBeenCalledWith(
      '/api/v1/atlas/jobs',
      expect.objectContaining({
        method: 'POST',
        ownerToken: 'tok',
        label: 'Atlas job submit API error',
      }),
    );
    const body = JSON.parse(apiFetch.apiFetch.mock.calls[0][1].body);
    expect(body).toMatchObject({
      session_id: 'sess-1',
      atlas: { driver: 'category', categoryProperty: 'district', includeCover: true },
      classification_scope: 'shared',
      target_dpi: 300,
    });
    expect(resp.plan?.page_count).toBe(13);
    expect(resp.idempotent_reuse).toBe(false);
  });
});

describe('getAtlasJob', () => {
  it('GET /api/v1/atlas/jobs/{id}（编码）并透传 artifacts', async () => {
    apiFetch.apiFetch.mockResolvedValue({
      job: { id: '7', kind: 'atlas_export', status: 'completed' },
      pages: [{ page_id: 'cover', title: '', page_number: 1, status: 'rendered', error: null }],
      artifacts: {
        pdf: 'atlas_7_1.pdf',
        manifest: 'atlas_7_1.manifest.json',
        pdf_url: '/api/v1/export/download/atlas_7_1.pdf',
        manifest_url: '/api/v1/export/download/atlas_7_1.manifest.json',
        lineage_recorded: true,
      },
    });
    const detail = await getAtlasJob('7');
    expect(apiFetch.apiFetch).toHaveBeenCalledWith(
      '/api/v1/atlas/jobs/7',
      expect.objectContaining({ method: 'GET' }),
    );
    expect(detail.artifacts.pdf_url).toContain('/export/download/');
    expect(detail.pages[0].status).toBe('rendered');
  });

  it('job id 含特殊字符时编码', async () => {
    apiFetch.apiFetch.mockResolvedValue({ job: {}, pages: [], artifacts: {} });
    await getAtlasJob('a b/c');
    expect(apiFetch.apiFetch.mock.calls[0][0]).toBe('/api/v1/atlas/jobs/a%20b%2Fc');
  });
});
