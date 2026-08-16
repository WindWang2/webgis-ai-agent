/**
 * #551: SkillsHub must not render fake controls.
 *   - skills[].enabled toggle removed (zero consumers — ChatRequest has no
 *     skills field, engine filter would need b11 tool-dispatch change).
 *   - "Upload Custom Skill" previously had NO onClick — now wired to the real
 *     backend POST /api/v1/config/skills/upload (admin-only, multipart file).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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
      category: '工作流',
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
});