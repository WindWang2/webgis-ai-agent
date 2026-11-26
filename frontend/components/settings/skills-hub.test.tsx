/**
 * #551: SkillsHub must not render fake controls.
 *   - skills[].enabled toggle removed (zero consumers — ChatRequest has no
 *     skills field, engine filter would need b11 tool-dispatch change).
 *   - "Upload Custom Skill" previously had NO onClick — now wired to the real
 *     backend POST /api/v1/config/skills/upload (admin-only, multipart file).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockState } = vi.hoisted(() => ({
  mockState: {
    skills: [] as unknown[],
    setSkills: (skills: unknown[]) => {
      mockState.skills = skills;
    },
  },
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
}));

import { SkillsHub } from './skills-hub';
import { invalidateCache } from '@/lib/api/get-fast-path';

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
let uploadResponse: () => ReturnType<typeof jsonOk | typeof jsonErr>;

function routeFetch() {
  fetchMock.mockImplementation((url: string | URL) => {
    const u = String(url);
    if (u.includes('/api/v1/chat/skills')) {
      return Promise.resolve(
        jsonOk({ skills: [{ name: 'poi', description: '通过 Overpass 查询兴趣点' }] })
      );
    }
    if (u.includes('/api/v1/config/skills/upload')) {
      return Promise.resolve(uploadResponse());
    }
    return Promise.resolve(jsonOk({ code: 'SUCCESS', success: true, data: null }));
  });
}

describe('SkillsHub (#551)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    uploadResponse = () => jsonOk({ status: 'ok', filename: 'my_skill.py' });
    mockState.skills = [];
    invalidateCache('/api/v1/chat/skills');
    routeFetch();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the real backend skill catalogue (read-only, no fake toggle)', async () => {
    const { rerender } = render(<SkillsHub />);

    // fetch 结果映射进 store（契约：目录 -> SkillEntry）
    await waitFor(() => expect(mockState.skills).toHaveLength(1));
    expect(mockState.skills[0]).toEqual({
      id: 'poi',
      name: 'poi',
      desc: '通过 Overpass 查询兴趣点',
      category: 'Other',
    });

    rerender(<SkillsHub />);
    expect(screen.getByText('poi')).toBeInTheDocument();

    // fake enablement toggle is gone
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    expect(screen.queryByText(/启用技能/i)).not.toBeInTheDocument();
  });

  it('Upload button triggers a real multipart POST to /config/skills/upload', async () => {
    const { rerender } = render(<SkillsHub />);
    await waitFor(() => expect(mockState.skills).toHaveLength(1));
    rerender(<SkillsHub />);

    const file = new File(['def run():\n    pass\n'], 'my_skill.py', { type: 'text/x-python' });
    const user = userEvent.setup();
    await user.upload(screen.getByTestId('skill-file-input'), file);

    await waitFor(() => {
      const uploadCall = fetchMock.mock.calls.find(([url]) => String(url).includes('/config/skills/upload'));
      expect(uploadCall).toBeDefined();
    });
    const [url, init] = fetchMock.mock.calls.find(([u]) => String(u).includes('/config/skills/upload'))!;
    expect(String(url)).toContain('/api/v1/config/skills/upload');
    expect(init?.method).toBe('POST');
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).get('file')).toEqual(file);

    expect(await screen.findByText(/已上传并热加载：my_skill\.py/)).toBeInTheDocument();
  });

  it('403 from the upload endpoint renders the admin-required message', async () => {
    uploadResponse = () => jsonErr(403, 'Forbidden', { detail: '权限不足' });
    const { rerender } = render(<SkillsHub />);
    await waitFor(() => expect(mockState.skills).toHaveLength(1));
    rerender(<SkillsHub />);

    const file = new File(['def run():\n    pass\n'], 'bad.py', { type: 'text/x-python' });
    const user = userEvent.setup();
    await user.upload(screen.getByTestId('skill-file-input'), file);

    expect(await screen.findByText(/需要管理员权限才能上传技能/)).toBeInTheDocument();
  });

  it('empty backend catalogue clears the store (no demo fallback)', async () => {
    // 空目录部署：后端返回 []，store 里残留的 demo 技能必须被清掉。
    mockState.skills = [
      { id: 'poi', name: 'POI 查询', desc: 'demo', category: '数据获取' },
    ];
    fetchMock.mockImplementation((url: string | URL) => {
      const u = String(url);
      if (u.includes('/api/v1/chat/skills')) {
        return Promise.resolve(jsonOk({ skills: [] }));
      }
      return Promise.resolve(jsonOk({ code: 'SUCCESS', success: true, data: null }));
    });
    const { rerender } = render(<SkillsHub />);
    await waitFor(() => expect(mockState.skills).toHaveLength(0));
    rerender(<SkillsHub />);
    expect(screen.queryByText('POI 查询')).not.toBeInTheDocument();
  });

  it('panel title goes through i18n (no hardcoded English heading)', () => {
    // P3 i18n：title 曾是唯一不走 t() 的设置面板标题（其余面板均键化）。
    render(<SkillsHub />);
    expect(screen.getByText('技能中心')).toBeInTheDocument();
    expect(screen.queryByText('Skills Hub')).not.toBeInTheDocument();
  });

  it('upload button label is localized in idle and failure states', async () => {
    // P3 i18n：'Upload Custom Skill' / 'Failed' 曾是硬编码英文，zh 下渲染英文。
    uploadResponse = () => jsonErr(500, 'Internal Server Error', { detail: 'boom' });
    const { rerender } = render(<SkillsHub />);
    await waitFor(() => expect(mockState.skills).toHaveLength(1));
    rerender(<SkillsHub />);

    expect(screen.getByRole('button')).toHaveTextContent('上传自定义技能');
    expect(screen.queryByText('Upload Custom Skill')).not.toBeInTheDocument();

    const file = new File(['def run():\n    pass\n'], 'bad.py', { type: 'text/x-python' });
    const user = userEvent.setup();
    await user.upload(screen.getByTestId('skill-file-input'), file);

    await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent('上传失败'));
    expect(screen.queryByText('Failed')).not.toBeInTheDocument();
  });

  it('success upload resets to idle instead of sticking on the result label', async () => {
    // P3 i18n：success 态此前永不回 idle，按钮永久停在 'Uploaded' 英文标签。
    vi.useFakeTimers();
    try {
      render(<SkillsHub />);
      // fetch mock 全走 microtask；fake timers 只接管 setTimeout。
      const flush = async () => {
        for (let i = 0; i < 25; i += 1) await Promise.resolve();
      };
      await act(async () => {
        await flush();
      });

      const file = new File(['def run():\n    pass\n'], 'my_skill.py', { type: 'text/x-python' });
      await act(async () => {
        fireEvent.change(screen.getByTestId('skill-file-input'), { target: { files: [file] } });
        await flush();
      });

      // 成功瞬间按钮就回到可复用的动作标签，不再出现 'Uploaded' 终态标签。
      expect(screen.getByRole('button')).toHaveTextContent('上传自定义技能');
      expect(screen.queryByText('Uploaded')).not.toBeInTheDocument();
      expect(screen.getByText(/已上传并热加载：my_skill\.py/)).toBeInTheDocument();

      // 5s 后 upload 状态回 idle，结果状态行消失。
      await act(async () => {
        vi.advanceTimersByTime(5000);
      });
      expect(screen.queryByText(/已上传并热加载：my_skill\.py/)).not.toBeInTheDocument();
      expect(screen.getByRole('button')).toHaveTextContent('上传自定义技能');
    } finally {
      vi.useRealTimers();
    }
  });
});