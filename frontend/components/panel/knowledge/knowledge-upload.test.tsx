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

  it('非 UTF-8 字节（Latin-1 / Shift-JIS / UTF-16LE）显式拒收，不静默解出乱码', async () => {
    render(<KnowledgeUpload onUploaded={vi.fn()} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const cases: Array<[string, number[]]> = [
      // Latin-1 "caféA"（E9 41）在 GB18030 下可解为 "caf锳"
      ['latin1.txt', [0x63, 0x61, 0x66, 0xe9, 0x41]],
      // Shift-JIS「ありがとう」
      ['sjis.txt', [0x82, 0xa0, 0x82, 0xc2, 0x82, 0xdc, 0x82, 0xb5]],
      // 无 BOM UTF-16LE「中文」
      ['utf16le.txt', [0x2d, 0x4e, 0x87, 0x41]],
    ];

    for (const [name, bytes] of cases) {
      fireEvent.change(input, {
        target: { files: [new File([new Uint8Array(bytes)], name, { type: 'text/plain' })] },
      });
      expect(
        await screen.findByText(`无法读取「${name}」（可能不是 UTF-8 文本）。`),
      ).toBeInTheDocument();
      expect(screen.queryByText(/已读取/)).not.toBeInTheDocument();
    }
    expect(mockFetch).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /索引文档/ })).toBeDisabled();
  });

  it('新选择被拒（类型/大小/读取失败）时清空上一文件，避免误索引旧文档', async () => {
    render(<KnowledgeUpload onUploaded={vi.fn()} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const loadNotes = async () => {
      fireEvent.change(input, { target: { files: [makeTextFile('notes.txt', '旧文档正文')] } });
      expect(await screen.findByText(/已读取：notes\.txt/)).toBeInTheDocument();
      expect(screen.getByLabelText('标题')).toHaveValue('notes');
    };

    await loadNotes();
    fireEvent.change(input, { target: { files: [makeTextFile('paper.pdf', '%PDF', 'application/pdf')] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('后端尚不支持');
    expect(screen.queryByText(/已读取/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('标题')).toHaveValue('');
    expect(screen.getByRole('button', { name: /索引文档/ })).toBeDisabled();

    await loadNotes();
    fireEvent.change(input, {
      target: { files: [new File([new Uint8Array([0xff, 0xfe])], 'broken.txt', { type: 'text/plain' })] },
    });
    expect(await screen.findByText(/无法读取「broken\.txt」/)).toBeInTheDocument();
    expect(screen.queryByText(/已读取/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('标题')).toHaveValue('');
    expect(screen.queryByLabelText('已读取的文档内容（可编辑）')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /索引文档/ })).toBeDisabled();

    await loadNotes();
    const oversized = makeTextFile('huge.txt', 'x');
    Object.defineProperty(oversized, 'size', { value: 100 * 1024 * 1024 + 1 });
    fireEvent.change(input, { target: { files: [oversized] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('文件超出后端上限');
    expect(screen.queryByText(/已读取/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('标题')).toHaveValue('');
    expect(screen.getByRole('button', { name: /索引文档/ })).toBeDisabled();
    expect(mockFetch).not.toHaveBeenCalled();
  });
});
