/**
 * VisualAttachBar 本地缩略图 blob URL 回收（评审 P2）。
 *
 * 每次附图都会 URL.createObjectURL 一张地图截图 PNG；旧实现从不 revoke ——
 * removeDraft / 切换会话 / 发送消费后草稿消失都不回收，会话内不断累积泄漏。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';

import { VisualAttachBar } from './visual-attach';
import { useVisualAttachStore } from '@/lib/visual-context/attach-store';

const revokeSpy = vi.fn();

beforeEach(() => {
  useVisualAttachStore.setState({ sessionId: null, drafts: [] });
  revokeSpy.mockClear();
  // jsdom 不实现 URL.createObjectURL/revokeObjectURL —— 打桩以便观测回收。
  (URL as unknown as { revokeObjectURL?: (u: string) => void }).revokeObjectURL = revokeSpy;
});

afterEach(() => {
  delete (URL as unknown as { revokeObjectURL?: (u: string) => void }).revokeObjectURL;
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
});
