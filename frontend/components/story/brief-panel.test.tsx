/**
 * BriefPanel moveBlock 半途失败自愈测试（P3-frontend_components_story-00）。
 *
 * 场景：moveBlock 交换 order 的第二次 patch 失败（HTTP 500）而第一次已生效时，
 * mutate 的 catch 必须重拉服务端（reload），使本地 revision / 顺序回到服务端
 * 真相；否则本地无限期显示移动前旧序，直到下次 409 冲突才自愈。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

const toastStore = { toasts: [] as unknown[], addToast: vi.fn(), removeToast: vi.fn() };
// 组件经 useToastStore.getState().addToast 直接发 toast（不走 hook selector），
// mock 同时提供 selector 调用形态与 getState。
vi.mock('@/components/ui/toast', () => ({
  useToastStore: Object.assign(
    (selector: (s: typeof toastStore) => unknown) => selector(toastStore),
    { getState: () => toastStore },
  ),
}));

// 确定性 translator：key 原样返回，便于按 aria-label / 文案断言。
// 注意返回稳定引用 —— 组件 useCallback 依赖 t，每次渲染换新函数会反复触发
// reload effect（真实 useT 由 i18n provider memo，本就稳定）。
vi.mock('@/lib/i18n/useT', () => {
  const t = (key: string) => key;
  return { useT: () => t };
});

const briefApi = vi.hoisted(() => ({
  createBriefDraft: vi.fn(),
  deleteBriefBlock: vi.fn(),
  downloadBriefReleaseHtml: vi.fn(),
  freezeBriefRelease: vi.fn(),
  getBrief: vi.fn(),
  inspectBriefEvidence: vi.fn(),
  listBriefReleases: vi.fn(),
  patchBriefBlock: vi.fn(),
  regenerateBrief: vi.fn(),
  revokeBriefRelease: vi.fn(),
  runBriefPreflight: vi.fn(),
  shareBriefRelease: vi.fn(),
}));
vi.mock('@/lib/api/brief', () => briefApi);

import { BriefPanel } from './brief-panel';
import { ApiError } from '@/lib/api/transport';
import type { BriefResponseDto } from '@/lib/api/brief';

/** 章节 ch-1 下两个 text 块：A(order 0) / B(order 1)。 */
function makeBrief(revision: number): BriefResponseDto {
  return {
    brief_id: 'b-1',
    revision,
    stale: [],
    summary: {
      brief_id: 'b-1',
      revision,
      title: '测试简报',
      chapters: 1,
      blocks: 2,
      facts: 0,
      fact_stats: { verified: 0, warning: 0, unverified: 0 },
    },
    spec: {
      brief_schema_version: '1',
      metadata: { title: '测试简报', session_id: 's-1' },
      chapters: [{ id: 'ch-1', title: '章节一', narrative: '', arc_role: 'introduction' }],
      camera_keyframes: [],
      linked_widgets: [],
      blocks: [
        { id: 'blk-a', chapter_id: 'ch-1', kind: 'text', order: 0, origin: 'auto', locked: false, text: 'AAA' },
        { id: 'blk-b', chapter_id: 'ch-1', kind: 'text', order: 1, origin: 'auto', locked: false, text: 'BBB' },
      ],
      facts: [],
      provenance: {
        session_id: 's-1',
        mapspec_fingerprint: 'fp',
        artifact_refs: [],
        generator: 'test',
      },
    },
  };
}

const noOwnerToken = async (): Promise<string | null> => null;
const noop = (): void => undefined;

beforeEach(() => {
  vi.clearAllMocks();
  toastStore.addToast.mockClear();
  briefApi.listBriefReleases.mockResolvedValue({ releases: [] });
});

describe('BriefPanel moveBlock 失败自愈', () => {
  it('第二次 patch 失败（首个已生效）后重拉服务端，不滞留旧序', async () => {
    // 首次加载 revision 7；失败后的 reload 返回服务端真相：revision 8、
    // A/B 两块 order 均为 1（第一次 patch 已生效、第二次 500）。
    briefApi.getBrief
      .mockResolvedValueOnce(makeBrief(7))
      .mockResolvedValueOnce({
        ...makeBrief(8),
        spec: {
          ...makeBrief(8).spec,
          blocks: [
            { id: 'blk-a', chapter_id: 'ch-1', kind: 'text', order: 1, origin: 'auto', locked: false, text: 'AAA' },
            { id: 'blk-b', chapter_id: 'ch-1', kind: 'text', order: 1, origin: 'auto', locked: false, text: 'BBB' },
          ],
        },
      });
    briefApi.patchBriefBlock
      .mockResolvedValueOnce({ saved: true, brief_id: 'b-1', revision: 8, title: '测试简报' })
      .mockRejectedValueOnce(
        new ApiError(500, 'Internal Server Error', null, 'req-err', 'briefUpdateFailed'),
      );

    render(
      <BriefPanel
        sessionId="s-1"
        onRequestOwnerToken={noOwnerToken}
        onClose={noop}
      />,
    );
    await screen.findAllByTestId('brief-text-block');
    expect(briefApi.getBrief).toHaveBeenCalledTimes(1);
    expect(briefApi.getBrief).toHaveBeenLastCalledWith('s-1', { ownerToken: null });

    // A 块下移：第一次 patch（A → order 1）成功，第二次（B → order 0）500。
    await act(async () => {
      fireEvent.click(screen.getAllByLabelText('briefMoveDownAria')[0]);
    });

    await waitFor(() => expect(briefApi.patchBriefBlock).toHaveBeenCalledTimes(2));
    expect(briefApi.patchBriefBlock).toHaveBeenNthCalledWith(
      1, 's-1', 'blk-a', { order: 1 }, 7, { ownerToken: null },
    );
    expect(briefApi.patchBriefBlock).toHaveBeenNthCalledWith(
      2, 's-1', 'blk-b', { order: 0 }, 8, { ownerToken: null },
    );
    // 错误 toast 仍然展示。
    expect(toastStore.addToast).toHaveBeenCalledWith(
      expect.stringContaining('briefUpdateFailed'), 'error',
    );
    // 核心回归点：非 409 失败也重拉，本地同步到服务端真相（revision 8）。
    await waitFor(() => expect(briefApi.getBrief).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('rev 8')).toBeInTheDocument();
  });
});
