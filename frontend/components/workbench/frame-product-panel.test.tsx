/**
 * G15 WP07 —— FrameProductPanel 用户旅程（组件级）：
 * 三幅画幅恢复显示 → 改第二幅标题（CAS 提交 set_frame_product patch）→
 * 激活第三幅。身份徽标/共享分级摘要/空态一并断言。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { resetLiveState, setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const fetchMock = vi.fn();

beforeEach(() => {
  resetLiveState();
  fetchMock.mockReset();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  resetLiveState();
});

function threeFrameSpec() {
  const feats: unknown[] = [];
  return {
    version: '2.0',
    revision: 12,
    layout: {
      frames: [
        { id: 'fp-t-2020', title: '2020', extent: [1, 2, 3, 4] },
        { id: 'fp-t-2022', title: '2022', extent: [1, 2, 3, 4] },
        { id: 'fp-t-2024', title: '2024', extent: [1, 2, 3, 4] },
      ],
    },
    frame_product: {
      schema_version: 'frame_product.v1',
      enabled: true,
      product_kind: 'time_series',
      grid: { columns: 3, title: '成都三年' },
      frames: [
        { frame_id: 'fp-t-2020', legend: 'shared',
          data: { ref: 'r', layer_id: 'l', field: 'v', content_revision: 2,
                  time_window: { start: '2020-01-01T00:00:00Z', end: '2021-01-01T00:00:00Z' } } },
        { frame_id: 'fp-t-2022', legend: 'shared',
          data: { ref: 'r', layer_id: 'l', field: 'v', content_revision: 2,
                  time_window: { start: '2022-01-01T00:00:00Z', end: '2023-01-01T00:00:00Z' } } },
        { frame_id: 'fp-t-2024', legend: 'independent' },
      ],
      shared_expression: {
        field: 'per_10k', domain_status: 'complete',
        breaks: [0, 5, 10, 15], k: 3, palette: 'Blues',
      },
      active_frame: 'fp-t-2020',
    },
    layers: [],
    sources: {},
    ...feats,
  };
}

async function primeCursorWithSpec(spec: unknown, revision = 12) {
  const { commitMapSpecDocument } = await import('@/lib/mapspec/session-cursor');
  // revision 经 cursor 整体重置（commitMapSpecDocument 只校验不设值）。
  setMapSpecSessionCursor('sess-g15', revision);
  commitMapSpecDocument(spec, revision);
}

describe('FrameProductPanel', () => {
  it('空态：未绑定产品时给出可行动提示', async () => {
    const { FrameProductPanel } = await import(
      '@/components/workbench/frame-product-panel');
    await primeCursorWithSpec({ version: '2.0', layers: [] });
    render(<FrameProductPanel />);
    expect(await screen.findByTestId('frame-product-empty')).toBeTruthy();
  });

  it('三幅恢复 + 改第二幅标题经 CAS patch + 激活第三幅', async () => {
    const { FrameProductPanel } = await import(
      '@/components/workbench/frame-product-panel');
    const spec = threeFrameSpec();
    await primeCursorWithSpec(spec);

    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? '{}')) as {
        intent: string; patch: Record<string, unknown>;
      };
      const next = JSON.parse(JSON.stringify(spec));
      if (body.patch.active_frame) next.frame_product.active_frame = body.patch.active_frame;
      if (Array.isArray(body.patch.frames)) next.frame_product.frames = body.patch.frames;
      return new Response(JSON.stringify({
        status: 'ok', mutation_revision: 13, mapspec: next,
      }), { status: 200, headers: { 'content-type': 'application/json' } });
    });

    render(<FrameProductPanel />);
    const list = await screen.findByTestId('frame-product-frames');
    expect(list.children.length).toBe(3);
    // 共享分级摘要可见。
    const sharedEl = await screen.findByTestId('frame-product-shared');
    expect(sharedEl.textContent).toContain('per_10k');
    // 激活徽标在第一幅。
    expect(list.children[0].getAttribute('data-frame-id')).toBe('fp-t-2020');

    // 改第二幅标题（blur 提交）。
    const titleInputs = screen.getAllByLabelText('画幅标题');
    fireEvent.change(titleInputs[1], { target: { value: '2022（更新）' } });
    fireEvent.blur(titleInputs[1]);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const sent = JSON.parse(
      String(fetchMock.mock.calls[0][1]?.body ?? '{}')) as {
        intent: string; expected_revision: number;
        patch: { frames?: Array<{ frame_id: string; title?: string }> };
      };
    expect(sent.intent).toBe('set_frame_product');
    expect(sent.expected_revision).toBe(12);
    expect(sent.patch.frames?.[1]).toMatchObject({
      frame_id: 'fp-t-2022', title: '2022（更新）',
    });
    // frame_id 身份与顺序保持。
    expect(sent.patch.frames?.map((f) => f.frame_id)).toEqual([
      'fp-t-2020', 'fp-t-2022', 'fp-t-2024']);

    // 激活第三幅（active_frame patch）。
    const activateBtn = screen.getByLabelText('激活画幅 fp-t-2024');
    fireEvent.click(activateBtn);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const sent2 = JSON.parse(
      String(fetchMock.mock.calls[1][1]?.body ?? '{}')) as {
        patch: { active_frame?: string };
      };
    expect(sent2.patch.active_frame).toBe('fp-t-2024');
  });
});
