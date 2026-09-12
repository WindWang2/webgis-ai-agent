/**
 * 知识库 API 模块 — ApiResponse 信封展开与错误传播（V9 knowledge-market-ui）。
 *
 * 后端契约（frontend/docs/knowledge-market-recon.md）：/api/v1/knowledge/*
 * 全部走 ApiResponse<T> 信封；success=false 或 data=null 时本模块必须抛错，
 * 绝不能把空壳当成功（诚实空态的上游约束）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  addKnowledgeDocument,
  deleteKnowledgeDocument,
  knowledgeFileTypeForFileName,
  listKnowledgeDocuments,
  searchKnowledge,
} from './knowledge';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  headers: { get: () => null },
  text: () => Promise.resolve(JSON.stringify(body)),
});

const envelope = <T,>(data: T) => ({ code: 'SUCCESS', success: true, message: 'ok', data });

beforeEach(() => {
  vi.clearAllMocks();
});

describe('knowledgeFileTypeForFileName', () => {
  it('maps supported text extensions to backend file_type vocab', () => {
    expect(knowledgeFileTypeForFileName('a.txt')).toBe('text');
    expect(knowledgeFileTypeForFileName('b.MD')).toBe('markdown');
    expect(knowledgeFileTypeForFileName('c.markdown')).toBe('markdown');
    expect(knowledgeFileTypeForFileName('d.json')).toBe('json');
  });

  it('rejects formats the backend cannot index (no multipart/PDF support)', () => {
    expect(knowledgeFileTypeForFileName('e.pdf')).toBeNull();
    expect(knowledgeFileTypeForFileName('f.docx')).toBeNull();
    expect(knowledgeFileTypeForFileName('g')).toBeNull();
  });
});

describe('listKnowledgeDocuments', () => {
  it('unwraps the ApiResponse envelope and forwards pagination params', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk(
        envelope({
          total: 2,
          items: [
            {
              id: 'doc_a',
              title: 'T',
              file_type: 'text',
              chunk_count: 3,
              status: 'completed',
              created_at: null,
            },
          ],
        }),
      ),
    );
    const res = await listKnowledgeDocuments({ limit: 20, offset: 40 });
    expect(res.total).toBe(2);
    expect(res.items[0]?.id).toBe('doc_a');
    const url = String(mockFetch.mock.calls[0][0]);
    expect(url).toContain('/api/v1/knowledge/documents');
    expect(url).toContain('limit=20');
    expect(url).toContain('offset=40');
  });

  it('throws when the envelope reports failure (never returns a hollow success)', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({ code: 'ERR', success: false, message: 'boom', data: null }),
    );
    await expect(listKnowledgeDocuments()).rejects.toThrow('boom');
  });
});

describe('addKnowledgeDocument', () => {
  it('posts plain-text JSON with snake_case file_type', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk(envelope({ document_id: 'doc_new', chunk_count: 5, status: 'completed' })),
    );
    const res = await addKnowledgeDocument({
      title: '说明',
      content: '正文',
      fileType: 'markdown',
    });
    expect(res.document_id).toBe('doc_new');
    expect(res.chunk_count).toBe(5);
    const [, init] = mockFetch.mock.calls[0];
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body);
    expect(body).toEqual({ title: '说明', content: '正文', file_type: 'markdown' });
  });
});

describe('searchKnowledge', () => {
  it('passes q/top_k/document_id and unwraps results verbatim (score stays raw L2)', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk(
        envelope({
          results: [
            {
              id: 'chk_1',
              document_id: 'doc_1',
              title: 'T',
              content: '全文',
              file_type: 'text',
              score: 0.35,
            },
          ],
        }),
      ),
    );
    const results = await searchKnowledge({ q: 'q', topK: 10, documentId: 'doc_1' });
    expect(results[0]?.score).toBe(0.35);
    const url = String(mockFetch.mock.calls[0][0]);
    expect(url).toContain('/api/v1/knowledge/search?');
    expect(url).toContain('q=q');
    expect(url).toContain('top_k=10');
    expect(url).toContain('document_id=doc_1');
  });
});

describe('deleteKnowledgeDocument', () => {
  it('encodes the document id into the delete path and tolerates null data', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(envelope(null)));
    await expect(deleteKnowledgeDocument('doc x/1')).resolves.toBeUndefined();
    const url = String(mockFetch.mock.calls[0][0]);
    expect(url).toContain(`/api/v1/knowledge/document/${encodeURIComponent('doc x/1')}`);
    const [, init] = mockFetch.mock.calls[0];
    expect(init.method).toBe('DELETE');
  });

  it('propagates failure message (creator-only delete can be denied)', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({ code: 'ERR', success: false, message: '删除失败或无权访问该文档', data: null }),
    );
    await expect(deleteKnowledgeDocument('doc_a')).rejects.toThrow('删除失败或无权访问该文档');
  });
});
