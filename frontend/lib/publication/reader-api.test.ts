/**
 * 读者 API 客户端测试（WP3）—— share code 校验/端点路径/skipAuth。
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchReaderDoc, fetchReaderFeatures, sharedBase } from './reader-api';

const apiFetchMock = vi.fn();
vi.mock('@/lib/api/transport', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

afterEach(() => {
  apiFetchMock.mockReset();
});

describe('sharedBase', () => {
  it('合法 code 通过', () => {
    expect(sharedBase('Abcdef12_-x')).toBe('/api/v1/publications/shared/Abcdef12_-x');
  });
  it('非法形态拒绝（路径注入防御）', () => {
    expect(() => sharedBase('../secret')).toThrow();
    expect(() => sharedBase('short')).toThrow();
    expect(() => sharedBase('has%20space')).toThrow();
  });
});

describe('fetchReaderDoc', () => {
  it('GET doc 端点且 skipAuth', async () => {
    apiFetchMock.mockResolvedValue({ data: { spec_version: 'publication_snapshot.v1' } });
    const doc = await fetchReaderDoc('Abcdef12_-x');
    expect(doc.spec_version).toBe('publication_snapshot.v1');
    const [path, opts] = apiFetchMock.mock.calls[0];
    expect(path).toBe('/api/v1/publications/shared/Abcdef12_-x');
    expect((opts as { skipAuth: boolean }).skipAuth).toBe(true);
    expect((opts as { method: string }).method).toBe('GET');
  });
});

describe('fetchReaderFeatures', () => {
  it('where/bbox/fields → 查询串', async () => {
    apiFetchMock.mockResolvedValue({ data: { features: [] } });
    await fetchReaderFeatures('Abcdef12_-x', 'lyr-schools', {
      limit: 50,
      where: { field: 'students', op: 'gte', value: 400 },
      bbox: [1, 2, 3, 4],
      fields: ['students'],
    });
    const [path] = apiFetchMock.mock.calls[0];
    expect(path).toContain('/layers/lyr-schools/features');
    expect(path).toContain('limit=50');
    expect(path).toContain(encodeURIComponent(JSON.stringify({ field: 'students', op: 'gte', value: 400 })));
    expect(path).toContain('bbox=1%2C2%2C3%2C4');
    expect(path).toContain('fields=students');
  });
});
