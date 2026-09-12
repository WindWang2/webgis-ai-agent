/**
 * 知识库面板（rag-independent-panel 存根替换）测试 —
 * 抽屉结构 / tab 漫游 / 诚实空态与错误态 / 注入对话落 store / Escape 关闭。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { RagIndependentPanel } from './rag-independent-panel';
import { useHudStore } from '@/lib/store/useHudStore';

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

const hit = (n: number) => ({
  id: `chk_${n}`,
  document_id: `doc_${n}`,
  title: `文档${n}`,
  content: `这是第 ${n} 篇的分块全文内容`,
  file_type: 'text',
  score: 0.42 + n,
});

beforeEach(() => {
  vi.clearAllMocks();
  useHudStore.setState({
    pendingChatInjection: null,
    ragPanelOpen: false,
    settingsOpen: false,
  });
});

describe('RagIndependentPanel', () => {
  it('open=false 渲染 null（挂载兼容），open 后出现 dialog + 双 tab', () => {
    const { container } = render(<RagIndependentPanel open={false} onClose={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();

    mockFetch.mockResolvedValue(jsonOk(envelope({ total: 0, items: [] })));
    render(<RagIndependentPanel open onClose={vi.fn()} />);
    expect(screen.getByRole('dialog', { name: '知识库' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /文档/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /检索/ })).toBeInTheDocument();
  });

  it('文档空态是诚实空态（不造假数据），上传区可见', async () => {
    mockFetch.mockResolvedValue(jsonOk(envelope({ total: 0, items: [] })));
    render(<RagIndependentPanel open onClose={vi.fn()} />);
    expect(await screen.findByText('暂无已索引文档')).toBeInTheDocument();
    expect(screen.getByText('添加文档')).toBeInTheDocument();
  });

  it('列表加载失败出现 role=alert 错误（附重试）', async () => {
    mockFetch.mockRejectedValue(new Error('网络中断'));
    render(<RagIndependentPanel open onClose={vi.fn()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('网络中断');
  });

  it('检索：出结果 → 注入对话把 citation 文本写入 store 并请求关闭面板', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    mockFetch.mockResolvedValue(jsonOk(envelope({ total: 0, items: [] })));
    render(<RagIndependentPanel open onClose={onClose} />);

    await user.click(screen.getByRole('tab', { name: /检索/ }));
    await user.type(screen.getByLabelText('检索查询'), 'FAISS');
    mockFetch.mockResolvedValueOnce(jsonOk(envelope({ results: [hit(1)] })));
    await user.click(screen.getByRole('button', { name: /检索/ }));

    expect(await screen.findByText(/命中 1 条/)).toBeInTheDocument();
    expect(screen.getByText(/L2 距离 1\.4200/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '注入对话' }));
    const injected = useHudStore.getState().pendingChatInjection;
    expect(injected).not.toBeNull();
    expect(injected).toContain('[1] 来源：《文档1》 分块 chk_1');
    expect(injected).toContain('请基于以下知识库片段回答：FAISS [1]');
    expect(onClose).toHaveBeenCalled();
  });

  it('检索空结果：诚实空态（无命中），不渲染结果区', async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValue(jsonOk(envelope({ total: 0, items: [] })));
    render(<RagIndependentPanel open onClose={vi.fn()} />);
    await user.click(screen.getByRole('tab', { name: /检索/ }));
    // CJK 文本用 fireEvent 设值（userEvent 键盘模拟对 CJK 不可靠）。
    fireEvent.change(screen.getByLabelText('检索查询'), {
      target: { value: '不存在的主题' },
    });
    mockFetch.mockResolvedValueOnce(jsonOk(envelope({ results: [] })));
    await user.click(screen.getByRole('button', { name: /检索/ }));
    expect(await screen.findByText('无检索命中')).toBeInTheDocument();
    expect(screen.queryByText(/命中 \d+ 条/)).not.toBeInTheDocument();
  });

  it('Escape 关闭面板（useDialogFocus 契约）', async () => {
    mockFetch.mockResolvedValue(jsonOk(envelope({ total: 0, items: [] })));
    const onClose = vi.fn();
    render(<RagIndependentPanel open onClose={onClose} />);
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('tablist 方向键漫游（WAI-APG）', async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValue(jsonOk(envelope({ total: 0, items: [] })));
    render(<RagIndependentPanel open onClose={vi.fn()} />);
    const docsTab = screen.getByRole('tab', { name: /文档/ });
    const searchTab = screen.getByRole('tab', { name: /检索/ });
    // useDialogFocus 的初始聚焦（50ms 定时）落定后再开始漫游，避免时序竞争。
    await waitFor(() => expect(docsTab).toHaveFocus());
    await user.keyboard('{ArrowRight}');
    expect(searchTab).toHaveFocus();
    expect(searchTab).toHaveAttribute('aria-selected', 'true');
    await user.keyboard('{ArrowLeft}');
    expect(docsTab).toHaveFocus();
    expect(docsTab).toHaveAttribute('aria-selected', 'true');
  });
});
