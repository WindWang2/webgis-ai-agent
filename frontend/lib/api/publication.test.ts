import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { exportVectorPdf, vectorPdfErrorCode } from './publication';
import { ApiError } from './transport';

// transport 层桩：只断言请求形状与透传，不触网。
vi.mock('./transport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./transport')>();
  return {
    ...actual,
    apiFetch: vi.fn(),
  };
});

import { apiFetch } from './transport';

const mockedFetch = vi.mocked(apiFetch);

describe('exportVectorPdf 请求形状（C14 契约面）', () => {
  beforeEach(() => {
    mockedFetch.mockReset();
    mockedFetch.mockResolvedValue({
      success: true,
      filename: 'map_vector_1.pdf',
      url: '/api/v1/export/download/map_vector_1.pdf',
      format: 'pdf',
      vector: true,
      pages: 1,
      frames_rendered: 1,
      frames_skipped: 0,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('POST mapspec/title/target_dpi，无会话/atlas 时不带可选键', async () => {
    await exportVectorPdf({ mapspec: { version: '1.1' }, title: 't', targetDpi: 150 });
    expect(mockedFetch).toHaveBeenCalledWith(
      '/api/v1/export/vector-pdf',
      expect.objectContaining({
        method: 'POST',
        timeoutMs: 150_000,
      }),
    );
    const body = mockedFetch.mock.calls[0][1]?.body as Record<string, unknown>;
    expect(body).toEqual({ mapspec: { version: '1.1' }, title: 't', target_dpi: 150 });
    expect('session_id' in body).toBe(false);
    expect('atlas' in body).toBe(false);
  });

  it('atlas 策略与 session_id 原样透传（后端同形 camelCase atlas 面）', async () => {
    await exportVectorPdf({
      mapspec: {},
      sessionId: 'sess-1',
      atlas: {
        driver: 'category',
        layerId: 'l1',
        categoryProperty: 'zone',
        pageBudget: 8,
        includeCover: true,
        atlasTitle: 'Atlas',
      },
    });
    const body = mockedFetch.mock.calls[0][1]?.body as Record<string, unknown>;
    expect(body['session_id']).toBe('sess-1');
    expect(body['atlas']).toEqual({
      driver: 'category',
      layerId: 'l1',
      categoryProperty: 'zone',
      pageBudget: 8,
      includeCover: true,
      atlasTitle: 'Atlas',
    });
  });
});

describe('vectorPdfErrorCode typed 提取', () => {
  it('FastAPI detail envelope {code} → code', () => {
    const err = new ApiError(
      429,
      'Too Many Requests',
      { detail: { code: 'vector_pdf_busy', message: 'busy' } },
    );
    expect(vectorPdfErrorCode(err)).toBe('vector_pdf_busy');
  });

  it('detail 字符串 → 原样；无 body 时按状态码映射 503/429', () => {
    expect(vectorPdfErrorCode(new ApiError(400, 'Bad', { detail: 'atlas_policy_invalid' }))).toBe(
      'atlas_policy_invalid',
    );
    expect(vectorPdfErrorCode(new ApiError(503, 'x'))).toBe('vector_pdf_unavailable');
    expect(vectorPdfErrorCode(new ApiError(429, 'x'))).toBe('vector_pdf_busy');
    expect(vectorPdfErrorCode(new Error('x'))).toBeNull();
  });
});
