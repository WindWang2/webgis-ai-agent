import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      ragConfig: {
        spatialWeight: 60,
        topK: 5,
        rerank: true,
        vectorDb: '',
        collection: 'geoagent',
      },
      setRagConfig: vi.fn(),
      ragSpatial: [],
      ragSemantic: [],
    }),
}));

import { RagConfig } from './rag-config';

// ── Response doubles（与 transport.test.ts 同形）──
const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  json: () => Promise.resolve(body),
  text: () => Promise.resolve(JSON.stringify(body)),
});

const jsonErr = (status: number, statusText: string, body: unknown) => ({
  ok: false,
  status,
  statusText,
  json: () => Promise.resolve(body),
  text: () => Promise.resolve(typeof body === 'string' ? body : JSON.stringify(body)),
});

const fetchMock = vi.fn();

describe('RagConfig connectivity test (#390)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('对失败端点渲染 error 状态并展示后端错误详情（不再假成功）', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonErr(502, 'Bad Gateway', { detail: '知识库不可用: index.faiss 损坏' })
    );

    const user = userEvent.setup();
    render(<RagConfig />);

    await user.click(screen.getByRole('button', { name: /test connection/i }));

    expect(await screen.findByText('Failed')).toBeInTheDocument();
    expect(screen.getByText(/知识库不可用: index.faiss 损坏/)).toBeInTheDocument();

    // 请求确实 POST 到 /config/rag/test，并携带展示字段
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/api/v1/config/rag/test');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ address: '', collection: 'geoagent' });
  });

  it('测试成功后渲染 Connected 并展示后端返回的存储详情', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonOk({ status: 'ok', store: 'local-faiss', detail: '内置本地向量库（FAISS）就绪，已索引 0 个分块' })
    );

    const user = userEvent.setup();
    render(<RagConfig />);

    await user.click(screen.getByRole('button', { name: /test connection/i }));

    expect(await screen.findByText('Connected')).toBeInTheDocument();
    expect(screen.getByText(/已索引 0 个分块/)).toBeInTheDocument();
  });
});
