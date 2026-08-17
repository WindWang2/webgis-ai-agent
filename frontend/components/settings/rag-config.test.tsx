import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({
      ragConfig: {
        vectorDb: '',
        collection: 'geoagent',
      },
      setRagConfig: vi.fn(),
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

/** 按 URL 路由：/knowledge/documents 由每次用例的 docsResponse 决定。 */
let testResponse: () => ReturnType<typeof jsonOk | typeof jsonErr>;
let docsResponse: () => ReturnType<typeof jsonOk | typeof jsonErr>;

function routeFetch() {
  fetchMock.mockImplementation((url: string | URL) => {
    const u = String(url);
    if (u.includes('/api/v1/config/rag/test')) return Promise.resolve(testResponse());
    return Promise.resolve(docsResponse()); // knowledge/documents
  });
}

describe('RagConfig connectivity test (#390)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    testResponse = () => jsonOk({});
    docsResponse = () =>
      jsonOk({ code: 'SUCCESS', success: true, message: 'ok', data: { total: 0, items: [] } });
    routeFetch();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('对失败端点渲染 error 状态并展示后端错误详情（不再假成功）', async () => {
    testResponse = () => jsonErr(502, 'Bad Gateway', { detail: '知识库不可用: index.faiss 损坏' });

    const user = userEvent.setup();
    render(<RagConfig />);

    await user.click(screen.getByRole('button', { name: /test connection/i }));

    expect(await screen.findByText('Failed')).toBeInTheDocument();
    expect(screen.getByText(/知识库不可用: index.faiss 损坏/)).toBeInTheDocument();

    // 请求确实 POST 到 /config/rag/test，并携带展示字段
    const uploadCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/config/rag/test'))!;
    expect(uploadCall).toBeDefined();
    const [url, init] = uploadCall;
    expect(String(url)).toContain('/api/v1/config/rag/test');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ address: '', collection: 'geoagent' });
  });

  it('测试成功后渲染 Connected 并展示后端返回的存储详情', async () => {
    testResponse = () =>
      jsonOk({ status: 'ok', store: 'local-faiss', detail: '内置本地向量库（FAISS）就绪，已索引 0 个分块' });

    const user = userEvent.setup();
    render(<RagConfig />);

    await user.click(screen.getByRole('button', { name: /test connection/i }));

    expect(await screen.findByText('Connected')).toBeInTheDocument();
    expect(screen.getByText(/已索引 0 个分块/)).toBeInTheDocument();
  });

  it('#551: 假滑杆已移除（Spatial Weight / Top K / Rerank 无消费方）', () => {
    render(<RagConfig />);
    expect(screen.queryByText('Spatial Weight')).not.toBeInTheDocument();
    expect(screen.queryByText('Top K')).not.toBeInTheDocument();
    expect(screen.queryByText('Rerank')).not.toBeInTheDocument();
  });

  it('#551: 文档区真实消费 GET /api/v1/knowledge/documents', async () => {
    docsResponse = () =>
      jsonOk({
        code: 'SUCCESS',
        success: true,
        message: 'ok',
        data: {
          total: 2,
          items: [
            { id: 'd1', title: 'GIS空间分析方法论.pdf', file_type: 'pdf', chunk_count: 4, status: 'indexed' },
            { id: 'd2', title: '北京市空间数据手册v3.md', file_type: 'md', chunk_count: 2, status: 'indexed' },
          ],
        },
      });

    render(<RagConfig />);

    expect(await screen.findByText('GIS空间分析方法论.pdf')).toBeInTheDocument();
    expect(screen.getByText('北京市空间数据手册v3.md')).toBeInTheDocument();

    const [url] = fetchMock.mock.calls.find(([u]) => String(u).includes('/knowledge/documents'))!;
    expect(String(url)).toContain('/api/v1/knowledge/documents');
  });
});