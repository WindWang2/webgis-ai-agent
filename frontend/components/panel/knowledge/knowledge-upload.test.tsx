/**
 * KnowledgeUpload 组件测试 — 客户端读文件 / 类型与大小校验 / 粘贴模式 /
 * 同步索引在途态 / 诚实拒收（不伪装支持 PDF）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { KnowledgeUpload } from './knowledge-upload';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

const jsonOk = (body: unknown) => ({
  ok: true,
  status: 200,
  statusText: 'OK',
  headers: { get: () => null },
  text: () => Promise.resolve(JSON.stringify(body)),
});

const envelope = <T,>(data: T) => ({ code: 'SUCCESS', success: true, message: 'ok', data });

function makeTextFile(name: string, content: string, type = 'text/plain'): File {
  return new File([content], name, { type });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockFetch.mockReset();
});

describe('KnowledgeUpload', () => {
  it('文件模式：读取 .txt 后索引成功，展示 document_id 与分块数', async () => {
    const user = userEvent.setup();
    const onUploaded = vi.fn();
    render(<KnowledgeUpload onUploaded={onUploaded} />);

    // 仓库惯例：直接对 file input fireEvent.change（upload-zone.test.tsx 同款）。
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeTextFile('notes.txt', '知识内容')] } });
    expect(await screen.findByText(/已读取：notes\.txt/)).toBeInTheDocument();

    mockFetch.mockResolvedValueOnce(
      jsonOk(envelope({ document_id: 'doc_new', chunk_count: 4, status: 'completed' })),
    );
    await user.type(screen.getByLabelText('标题'), '笔记');
    await user.click(screen.getByRole('button', { name: /索引文档/ }));

    expect(await screen.findByRole('status')).toHaveTextContent('doc_new');
    expect(onUploaded).toHaveBeenCalledOnce();
    const [, init] = mockFetch.mock.calls[0];
    const body = JSON.parse(init.body);
    expect(body.file_type).toBe('text');
    expect(body.content).toBe('知识内容');
  });

  it('不支持的类型（PDF）被诚实拒收并说明后端缺口', async () => {
    render(<KnowledgeUpload onUploaded={vi.fn()} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeTextFile('paper.pdf', '%PDF', 'application/pdf')] } });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('后端尚不支持');
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('粘贴模式：标题+内容必填，POST 后清空内容', async () => {
    const user = userEvent.setup();
    render(<KnowledgeUpload onUploaded={vi.fn()} />);
    await user.click(screen.getByRole('tab', { name: '粘贴文本' }));

    await user.type(screen.getByLabelText('文档内容'), '粘贴的正文');
    await user.type(screen.getByLabelText('标题'), '粘贴文档');
    mockFetch.mockResolvedValueOnce(
      jsonOk(envelope({ document_id: 'doc_p', chunk_count: 1, status: 'completed' })),
    );
    await user.click(screen.getByRole('button', { name: /索引文档/ }));
    expect(await screen.findByRole('status')).toBeInTheDocument();
    expect(screen.getByLabelText('文档内容')).toHaveValue('');
  });

  it('后端失败时报错且不出现成功回执', async () => {
    const user = userEvent.setup();
    render(<KnowledgeUpload onUploaded={vi.fn()} />);
    await user.click(screen.getByRole('tab', { name: '粘贴文本' }));
    await user.type(screen.getByLabelText('文档内容'), 'x');
    await user.type(screen.getByLabelText('标题'), 't');
    mockFetch.mockResolvedValueOnce(
      jsonOk({ code: 'SERVER_ERROR', success: false, message: '文档上传失败: embed', data: null }),
    );
    await user.click(screen.getByRole('button', { name: /索引文档/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('文档上传失败');
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
  });
});
