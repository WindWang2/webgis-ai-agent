/**
 * 作者面发布 API 客户端测试（WP5）—— 端点路径 / ownerToken 头 / confirm 语义。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  publishPublication,
  createPublicationDraft,
  mintPublicationLink,
  previewPublication,
} from './publication-authoring';

const apiFetchMock = vi.fn();
vi.mock('@/lib/api/transport', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

afterEach(() => {
  apiFetchMock.mockReset();
});

describe('publication authoring api', () => {
  it('preview 携带 ownerToken 与 session_id', async () => {
    apiFetchMock.mockResolvedValue({ data: { layers: [] } });
    await previewPublication('sess-12345678', { selected_layer_ids: ['l1'] }, 'tok-abc');
    const [path, opts] = apiFetchMock.mock.calls[0];
    expect(path).toBe('/api/v1/publications/preview');
    expect((opts as { ownerToken?: string }).ownerToken).toBe('tok-abc');
    expect((opts as { body: { session_id: string } }).body.session_id).toBe('sess-12345678');
    expect((opts as { body: { selected_layer_ids?: string[] } }).body.selected_layer_ids).toEqual(['l1']);
  });

  it('publish 显式传 confirm 布尔（同意门在前端也可见）', async () => {
    apiFetchMock.mockResolvedValue({ data: { publication: {}, job: {} } });
    await publishPublication('pub-1', true, 'tok');
    const [path, opts] = apiFetchMock.mock.calls[0];
    expect(path).toBe('/api/v1/publications/pub-1/publish');
    expect((opts as { body: { confirm: boolean } }).body.confirm).toBe(true);
    expect((opts as { method: string }).method).toBe('POST');
  });

  it('link mint 携带 ttl_days', async () => {
    apiFetchMock.mockResolvedValue({ data: { share_code: 'c', share_url: '/p/c' } });
    await mintPublicationLink('pub-1', 7, 'tok');
    const [path, opts] = apiFetchMock.mock.calls[0];
    expect(path).toBe('/api/v1/publications/pub-1/link');
    expect((opts as { body: { ttl_days: number | null } }).body.ttl_days).toBe(7);
  });

  it('draft 创建路径正确', async () => {
    apiFetchMock.mockResolvedValue({ data: { id: 'x' } });
    await createPublicationDraft('sess-12345678', { license: 'CC0' }, 'tok');
    const [path] = apiFetchMock.mock.calls[0];
    expect(path).toBe('/api/v1/publications');
  });
});
