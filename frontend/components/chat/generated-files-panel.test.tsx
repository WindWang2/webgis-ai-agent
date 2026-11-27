import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { GeneratedFilesPanel } from './generated-files-panel';
import { downloadWithAuth } from '@/lib/api/authenticated-download';
import { listGeneratedFiles, type GeneratedFile } from '@/lib/api/generated-files';

vi.mock('@/lib/api/generated-files', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api/generated-files')>(),
  listGeneratedFiles: vi.fn(),
}));
vi.mock('@/lib/api/authenticated-download', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/api/authenticated-download')>(),
  downloadWithAuth: vi.fn(),
}));

const list = vi.mocked(listGeneratedFiles);
const download = vi.mocked(downloadWithAuth);
const panel = { sessionId: 'first', ownerToken: 'owner-a', messages: [], onBack: vi.fn() };
const map: GeneratedFile = {
  id: 'map', name: 'Zoning.png', format: 'png', source: 'export', created_at: 1700000000,
  download_url: '/api/v1/export/download/Zoning.png',
};
const report: GeneratedFile = {
  id: 'report', name: 'Atlas.pdf', format: 'pdf', source: 'report', created_at: null,
  download_url: null,
};
const messages = [{
  id: 'reply', role: 'assistant', content: '[roads.csv](/api/v1/export/download/roads.csv)',
}];

beforeEach(() => {
  localStorage.clear();
  list.mockReset().mockResolvedValue([]);
  download.mockReset().mockResolvedValue(undefined);
  panel.onBack.mockClear();
});

describe('GeneratedFilesPanel', () => {
  it('focuses the back button, exposes an empty state, and returns to chat', async () => {
    render(<GeneratedFilesPanel {...panel} />);
    const back = screen.getByRole('button', { name: '返回聊天' });
    expect(back).toHaveFocus();
    expect(await screen.findByText('暂无文件')).toBeVisible();
    expect(list).toHaveBeenCalledWith('first', { ownerToken: 'owner-a', signal: expect.any(AbortSignal) });
    fireEvent.click(back);
    expect(panel.onBack).toHaveBeenCalledOnce();
  });

  it('sorts dated files first, then supports name sorting, case-insensitive search and format filtering', async () => {
    list.mockResolvedValue([report, map]);
    render(<GeneratedFilesPanel {...panel} />);
    await screen.findByText(map.name);
    const rows = () => screen.getAllByRole('listitem');
    expect(rows()[0]).toHaveTextContent(map.name);
    expect(rows()[0].querySelector('time')).toHaveAttribute('datetime', '2023-11-14T22:13:20.000Z');
    expect(within(rows()[1]).getByText('暂不可下载')).toBeVisible();
    expect(within(rows()[1]).queryByRole('button', { name: /下载/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: '文件排序' }), { target: { value: 'name' } });
    expect(rows()[0]).toHaveTextContent(report.name);
    fireEvent.change(screen.getByRole('combobox', { name: '文件类型' }), { target: { value: 'PNG' } });
    expect(rows()).toHaveLength(1);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'ZONING' } });
    expect(rows()[0]).toHaveTextContent(map.name);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Atlas' } });
    expect(screen.getByText('没有匹配的文件')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '清除筛选' }));
    expect(rows()).toHaveLength(2);
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('keeps message files after a catalog failure, refreshes on demand, and does not refetch for streamed text', async () => {
    list.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([map]);
    const view = render(<GeneratedFilesPanel {...panel} messages={messages} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('文件列表刷新失败');
    expect(screen.getByText('roads.csv')).toBeVisible();
    view.rerender(<GeneratedFilesPanel {...panel} messages={[{ ...messages[0], content: messages[0].content + ' Done.' }]} />);
    expect(list).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '刷新文件列表' }));
    expect(await screen.findByText(map.name)).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByText('roads.csv')).toBeVisible();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('uses local message files without fetching a null session', async () => {
    render(<GeneratedFilesPanel {...panel} sessionId={null} messages={messages} />);
    expect(screen.getByText('roads.csv')).toBeVisible();
    expect(list).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '刷新文件列表' })).toBeDisabled();
  });

  it('aborts an old session request and ignores its late response, including after switching credentials', async () => {
    let finish!: (files: GeneratedFile[]) => void;
    list.mockResolvedValueOnce([report]).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const view = render(<GeneratedFilesPanel {...panel} />);
    expect(await screen.findByText(report.name)).toBeVisible();
    view.rerender(<GeneratedFilesPanel {...panel} ownerToken="owner-b" />);
    expect(screen.queryByText(report.name)).not.toBeInTheDocument();
    expect(screen.getByText('正在加载文件…')).toBeVisible();
    const tokenRequest = list.mock.calls[1][1]?.signal;
    view.rerender(<GeneratedFilesPanel {...panel} sessionId="second" ownerToken="owner-b" />);
    expect(tokenRequest?.aborted).toBe(true);
    expect(await screen.findByText('暂无文件')).toBeVisible();
    await act(async () => finish([map]));
    expect(screen.queryByText(map.name)).not.toBeInTheDocument();
    expect(list.mock.calls.map(([session]) => session)).toEqual(['first', 'first', 'second']);
  });

  it('downloads with the session token, reports failure, and allows retry', async () => {
    list.mockResolvedValue([map]);
    download.mockRejectedValueOnce(new Error('forbidden'));
    render(<GeneratedFilesPanel {...panel} />);
    const button = await screen.findByRole('button', { name: '下载 Zoning.png' });
    fireEvent.click(button);
    expect(await screen.findByRole('alert')).toHaveTextContent('无法下载 Zoning.png');
    expect(download).toHaveBeenCalledWith(map.download_url, { fallbackFilename: map.name, ownerToken: 'owner-a', signal: expect.any(AbortSignal) });
    fireEvent.click(button);
    await waitFor(() => expect(button).toBeEnabled());
    expect(download).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('cancels an in-flight download when leaving the session', async () => {
    list.mockResolvedValue([map]);
    download.mockImplementation(() => new Promise(() => {}));
    const view = render(<GeneratedFilesPanel {...panel} />);
    fireEvent.click(await screen.findByRole('button', { name: '下载 Zoning.png' }));
    expect(screen.getByRole('button', { name: '正在下载 Zoning.png' })).toBeDisabled();
    view.unmount();
    expect(download.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });

  it('keeps pins above the chosen sort, remembers them per session, and persists only unsigned keys', async () => {
    const signedReport = { ...report, download_url: '/api/v1/reports/123/download?signature=secret' };
    list.mockResolvedValue([map, signedReport]);
    const first = render(<GeneratedFilesPanel {...panel} />);
    const pin = await screen.findByRole('button', { name: '置顶 Atlas.pdf' });
    expect(screen.getAllByRole('listitem')[0]).toHaveTextContent(map.name);
    fireEvent.click(pin);
    expect(screen.getAllByRole('listitem')[0]).toHaveTextContent(report.name);
    expect(screen.getByRole('button', { name: '取消置顶 Atlas.pdf' })).toHaveAttribute('aria-pressed', 'true');
    expect(localStorage.length).toBe(1);
    const key = localStorage.key(0)!;
    expect(key).not.toContain('owner-a');
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual(['/api/v1/reports/123/download']);
    first.unmount();

    list.mockResolvedValue([map, { ...signedReport, download_url: '/api/v1/reports/123/download?signature=renewed' }]);
    const second = render(<GeneratedFilesPanel {...panel} />);
    expect(await screen.findByRole('button', { name: '取消置顶 Atlas.pdf' })).toBeVisible();
    expect(screen.getAllByRole('listitem')[0]).toHaveTextContent(report.name);
    second.rerender(<GeneratedFilesPanel {...panel} sessionId="second" />);
    expect(await screen.findByRole('button', { name: '置顶 Atlas.pdf' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getAllByRole('listitem')[0]).toHaveTextContent(map.name);
    second.rerender(<GeneratedFilesPanel {...panel} />);
    fireEvent.click(await screen.findByRole('button', { name: '取消置顶 Atlas.pdf' }));
    expect(screen.getAllByRole('listitem')[0]).toHaveTextContent(map.name);
    expect(localStorage.getItem(key)).toBeNull();
  });

  it('supports pinning on the page when browser storage is unavailable', async () => {
    list.mockResolvedValue([report, map]);
    vi.mocked(localStorage.getItem).mockImplementationOnce(() => { throw new Error('unavailable'); });
    vi.mocked(localStorage.setItem).mockImplementationOnce(() => { throw new Error('unavailable'); });
    render(<GeneratedFilesPanel {...panel} />);
    fireEvent.click(await screen.findByRole('button', { name: '置顶 Atlas.pdf' }));
    expect(screen.getByRole('button', { name: '取消置顶 Atlas.pdf' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getAllByRole('listitem')[0]).toHaveTextContent(report.name);
  });
});
