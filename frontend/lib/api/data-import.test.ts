/**
 * Data Import API client 测试 —— mock fetch（transport 纪律）+ XHR 创建通道。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

import {
  listImportBatches,
  getImportBatch,
  saveImportPlan,
  previewImportMembers,
  commitImportBatch,
  cancelImportBatch,
  getImportReceipts,
  deleteImportBatch,
} from './data-import';

function jsonOk(body: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}

describe('Data Import API', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.resetModules();
  });

  it('listImportBatches 携带 session_id query 并返回 batches', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ batches: [{ batch_id: 'ib-x', status: 'inspected' }] }));
    const out = await listImportBatches('sess-1');
    expect(mockFetch.mock.calls[0][0]).toContain('/api/v1/data-import/batches?session_id=sess-1');
    expect(out).toHaveLength(1);
    expect(out[0].batch_id).toBe('ib-x');
  });

  it('getImportBatch 路径含 batchId + session', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ batch_id: 'ib-abc', status: 'inspected', members: [] }));
    const out = await getImportBatch('ib-abc', 'sess-1');
    expect(mockFetch.mock.calls[0][0]).toContain('/api/v1/data-import/batches/ib-abc?session_id=sess-1');
    expect(out.batch_id).toBe('ib-abc');
  });

  it('saveImportPlan POST body 含 member_configs/selected', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ batch_id: 'ib-1', plan_version: 2, plan_hashes: {}, status: 'configured' }));
    const out = await saveImportPlan('ib-1', 'sess-1', {
      member_configs: { m1: { mode: 'points', x_column: 'x' } },
      selected: ['m1'],
      expected_plan_version: 1,
    });
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain('/plan?session_id=sess-1');
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body);
    expect(body.member_configs.m1.x_column).toBe('x');
    expect(body.expected_plan_version).toBe(1);
    expect(out.plan_version).toBe(2);
  });

  it('previewImportMembers 传 member_keys 与 row_limit', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ batch_id: 'ib-1', plan_version: 1, previews: [] }));
    await previewImportMembers('ib-1', 'sess-1', { member_keys: ['m1'], row_limit: 50 });
    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.member_keys).toEqual(['m1']);
    expect(body.row_limit).toBe(50);
  });

  it('commitImportBatch 空 body POST', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ batch_id: 'ib-1', status: 'committing', members: ['m1'], poll: '/x' }));
    const out = await commitImportBatch('ib-1', 'sess-1');
    expect(mockFetch.mock.calls[0][1].method).toBe('POST');
    expect(out.status).toBe('committing');
  });

  it('cancelImportBatch / getImportReceipts / deleteImportBatch 路由正确', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ batch_id: 'ib-1', status: 'cancelled' }));
    await cancelImportBatch('ib-1', 'sess-1');
    expect(mockFetch.mock.calls[0][0]).toContain('/cancel?session_id=sess-1');

    mockFetch.mockResolvedValueOnce(jsonOk({ batch_id: 'ib-1', status: 'completed', plan_version: 1, receipts: {}, notes: [] }));
    await getImportReceipts('ib-1', 'sess-1');
    expect(mockFetch.mock.calls[1][0]).toContain('/receipts?session_id=sess-1');

    mockFetch.mockResolvedValueOnce(jsonOk({ batch_id: 'ib-1', deleted: true }));
    const del = await deleteImportBatch('ib-1', 'sess-1');
    expect(mockFetch.mock.calls[2][1].method).toBe('DELETE');
    expect(del.deleted).toBe(true);
  });

  it('非 2xx 抛 ApiError，body 携带后端 detail', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: () => Promise.resolve(JSON.stringify({ detail: 'PLAN_VERSION_CONFLICT: x' })),
    });
    try {
      await saveImportPlan('ib-1', 'sess-1', {});
      expect.unreachable('should have thrown');
    } catch (e) {
      expect((e as { status: number }).status).toBe(409);
      const body = (e as { body?: { detail?: string } }).body;
      expect(body?.detail).toContain('PLAN_VERSION_CONFLICT');
    }
  });
});
