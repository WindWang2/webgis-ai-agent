/**
 * VisualAttachBar 本地缩略图 blob URL 回收（评审 P2）。
 *
 * 每次附图都会 URL.createObjectURL 一张地图截图 PNG；旧实现从不 revoke ——
 * removeDraft / 切换会话 / 发送消费后草稿消失都不回收，会话内不断累积泄漏。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, fireEvent, waitFor } from '@testing-library/react';

import { VisualAttachBar } from './visual-attach';
import { useVisualAttachStore } from '@/lib/visual-context/attach-store';

const uploadApi = vi.hoisted(() => ({ uploadVisualEvidence: vi.fn() }));
const captureApi = vi.hoisted(() => ({ captureCurrentMap: vi.fn() }));
vi.mock('@/lib/api/visual-context', () => ({ uploadVisualEvidence: uploadApi.uploadVisualEvidence }));
vi.mock('@/lib/visual-context/map-capture', () => ({
  captureCurrentMap: captureApi.captureCurrentMap,
}));

const revokeSpy = vi.fn();

beforeEach(() => {
  useVisualAttachStore.setState({ sessionId: null, drafts: [] });
  revokeSpy.mockClear();
  uploadApi.uploadVisualEvidence.mockReset();
  captureApi.captureCurrentMap.mockReset();
  // jsdom 不实现 URL.createObjectURL/revokeObjectURL —— 打桩以便观测回收。
  (URL as unknown as { revokeObjectURL?: (u: string) => void }).revokeObjectURL = revokeSpy;
});

afterEach(() => {
  delete (URL as unknown as { revokeObjectURL?: (u: string) => void }).revokeObjectURL;
  delete (URL as unknown as { createObjectURL?: (f: Blob) => string }).createObjectURL;
});

function addDraft(evidenceId: string, thumbUrl: string) {
  act(() => {
    useVisualAttachStore.getState().addDraft({
      evidenceId,
      sourceType: 'reference_upload',
      label: 'ref.png',
      thumbUrl,
    });
  });
}

describe('VisualAttachBar（blob URL 回收）', () => {
  it('草稿在列表里时不回收其缩略图 URL', () => {
    render(<VisualAttachBar sessionId="s1" ownerToken={null} />);
    addDraft('e-1', 'blob:thumb-1');
    expect(screen.getByTestId('visual-attach-chip-e-1')).toBeTruthy();
    expect(revokeSpy).not.toHaveBeenCalled();
  });

  it('移除草稿即回收对应 blob URL', () => {
    render(<VisualAttachBar sessionId="s1" ownerToken={null} />);
    addDraft('e-1', 'blob:thumb-1');
    act(() => { useVisualAttachStore.getState().removeDraft('e-1'); });
    expect(revokeSpy).toHaveBeenCalledWith('blob:thumb-1');
  });

  it('切换会话（store 清空草稿）即回收旧会话的 blob URL', () => {
    const view = render(<VisualAttachBar sessionId="s1" ownerToken={null} />);
    addDraft('e-1', 'blob:thumb-1');
    view.rerender(<VisualAttachBar sessionId="s2" ownerToken={null} />);
    expect(revokeSpy).toHaveBeenCalledWith('blob:thumb-1');
  });

  it('卸载时回收仍在登记的 blob URL', () => {
    const view = render(<VisualAttachBar sessionId="s1" ownerToken={null} />);
    addDraft('e-1', 'blob:thumb-1');
    view.unmount();
    expect(revokeSpy).toHaveBeenCalledWith('blob:thumb-1');
  });

  it('多选超出剩余名额时提示上限，不静默丢弃', async () => {
    uploadApi.uploadVisualEvidence.mockResolvedValue({
      evidence_id: 'e-picked', label: 'a.png', mapspec_revision: null, viewport_hash: 'h',
    });
    const createObjectURL = vi.fn((_file: Blob) => 'blob:picked');
    (URL as unknown as { createObjectURL?: (f: Blob) => string }).createObjectURL = createObjectURL;
    render(<VisualAttachBar sessionId="s1" ownerToken={null} />);
    // 已有 2 张 → 只剩 2 个名额，却选了 5 个文件
    addDraft('e-1', 'blob:thumb-1');
    addDraft('e-2', 'blob:thumb-2');
    const files = Array.from({ length: 5 }, (_, i) =>
      new File([`img-${i}`], `img-${i}.png`, { type: 'image/png' }));
    fireEvent.change(screen.getByLabelText('上传参考图'), { target: { files } });

    await waitFor(() => expect(uploadApi.uploadVisualEvidence).toHaveBeenCalledTimes(2));
    // 上限提示必须是可见 notice（status-critical），而非仅「最多 4 张」的禁用提示
    const notices = screen.getAllByText('最多 4 张附图');
    expect(notices.some((el) => el.className.includes('text-status-critical'))).toBe(true);
  });

  it('地图采集在草稿已满时显示错误且不上传', async () => {
    captureApi.captureCurrentMap.mockResolvedValue({
      blob: new Blob(['map'], { type: 'image/png' }),
      deviceWidth: 100,
      deviceHeight: 80,
      dpr: 1,
      viewport: { center: [0, 0], zoom: 10, bearing: 0, pitch: 0 },
    });
    (URL as unknown as { createObjectURL?: (f: Blob) => string }).createObjectURL =
      vi.fn(() => 'blob:map');
    render(<VisualAttachBar sessionId="s1" ownerToken={null} />);
    fireEvent.click(screen.getByTestId('visual-attach-button'));
    for (const id of ['e-1', 'e-2', 'e-3', 'e-4']) addDraft(id, `blob:${id}`);

    fireEvent.click(screen.getByText('当前地图视口'));

    await waitFor(() => {
      const notices = screen.getAllByText('最多 4 张附图');
      expect(notices.some((el) => el.className.includes('text-status-critical'))).toBe(true);
    });
    expect(uploadApi.uploadVisualEvidence).not.toHaveBeenCalled();
    expect(revokeSpy).toHaveBeenCalledWith('blob:map');
  });

  it('上传回执证据 id 重复时显示错误且不产生重复草稿', async () => {
    uploadApi.uploadVisualEvidence.mockResolvedValue({
      evidence_id: 'e-1', label: 'a.png', mapspec_revision: null, viewport_hash: 'h',
    });
    (URL as unknown as { createObjectURL?: (f: Blob) => string }).createObjectURL =
      vi.fn(() => 'blob:new');
    render(<VisualAttachBar sessionId="s1" ownerToken={null} />);
    addDraft('e-1', 'blob:existing');
    const file = new File(['img'], 'img.png', { type: 'image/png' });

    fireEvent.change(screen.getByLabelText('上传参考图'), { target: { files: [file] } });

    await waitFor(() => expect(uploadApi.uploadVisualEvidence).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText('附图失败')).toBeTruthy());
    expect(screen.getAllByTestId('visual-attach-chip-e-1')).toHaveLength(1);
    expect(revokeSpy).toHaveBeenCalledWith('blob:new');
  });
});
