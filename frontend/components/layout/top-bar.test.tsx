/**
 * #551: the Tweaks panel previously had NO production opener —
 * no code path ever called setTweaksOpen(true), so the panel was unreachable.
 * TopBar now renders a real "UI 调整" entry point wired to the store setter.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TopBar from './top-bar';
import { MOBILE_MAX_PX, THIN_MAX_PX } from '@/lib/hooks/use-layout-mode';

const mockSetTweaksOpen = vi.fn();
const mockSetSettingsOpen = vi.fn();
const mockSetHistoryOpen = vi.fn();
const mockSetIs3D = vi.fn();
const mockState = {
  leftPanelOpen: true,
  toggleLeftPanel: vi.fn(),
  aiStatus: 'idle',
  setSettingsOpen: mockSetSettingsOpen,
  setHistoryOpen: mockSetHistoryOpen,
  setTweaksOpen: mockSetTweaksOpen,
  is3D: false,
  setIs3D: mockSetIs3D,
};

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: typeof mockState) => unknown) => selector(mockState),
}));

vi.mock('@/lib/hooks/use-prefers-reduced-motion', () => ({
  usePrefersReducedMotion: () => false,
}));

vi.mock('@/components/map/baselayer-switcher', () => ({
  default: () => <div data-testid="baselayer-switcher" />,
}));

describe('TopBar tweaks opener (#551)', () => {
  it('renders a UI 调整 entry point that calls setTweaksOpen(true)', async () => {
    const user = userEvent.setup();
    render(<TopBar />);

    const opener = screen.getByRole('button', { name: 'UI 调整' });
    expect(opener).toBeInTheDocument();

    await user.click(opener);
    expect(mockSetTweaksOpen).toHaveBeenCalledWith(true);
  });
});

// ── #1667：390px 视口右侧集群折叠 ───────────────────────────────────────
//
// 缺陷：右侧操作集群（新建/历史/底图/3D/UI 调整/设置）固有宽度 ~530px，
// 390px 视口整体溢出屏外，「设置」按钮不可达。修复：mobile 档（≤768px，
// useLayoutMode 驱动）只保留 新建/设置 内联，其余收进「更多」收纳菜单。
//
// jsdom 无布局引擎（scrollWidth/clientWidth 恒 0），像素级断言由 e2e
// （j8 journey 的 scrollWidth ≤ clientWidth + 设置可达点击）承担；这里锁
// 折叠的结构契约：宽控件不内联、设置可达、收纳菜单内容齐备。

type MqlListener = (e: { matches: boolean }) => void;

class FakeMQL {
  private listeners = new Set<MqlListener>();
  constructor(public query: string, public matches: boolean) {}
  addEventListener(_t: string, l: MqlListener) {
    this.listeners.add(l);
  }
  removeEventListener(_t: string, l: MqlListener) {
    this.listeners.delete(l);
  }
  addListener(l: MqlListener) {
    this.listeners.add(l);
  }
  removeListener(l: MqlListener) {
    this.listeners.delete(l);
  }
  set(matches: boolean) {
    this.matches = matches;
    this.listeners.forEach((l) => l({ matches }));
  }
}

const mqls = new Map<string, FakeMQL>();

const MOBILE_QUERY = `(max-width: ${MOBILE_MAX_PX}px)`;
const THIN_QUERY = `(min-width: ${MOBILE_MAX_PX + 1}px) and (max-width: ${THIN_MAX_PX}px)`;

/** 装 matchMedia mock 并把视口设到 width（px）。 */
function installMatchMedia(width: number) {
  const make = (query: string): FakeMQL => {
    let mql = mqls.get(query);
    if (!mql) {
      mql = new FakeMQL(query, false);
      mqls.set(query, mql);
    }
    return mql;
  };
  make(MOBILE_QUERY);
  make(THIN_QUERY);
  for (const [query, mql] of mqls) {
    const matches =
      query === MOBILE_QUERY ? width <= MOBILE_MAX_PX : width > MOBILE_MAX_PX && width <= THIN_MAX_PX;
    if (mql.matches !== matches) act(() => mql.set(matches));
  }
  ;(window as unknown as { matchMedia: unknown }).matchMedia = vi.fn((query: string) => make(query));
}

describe('TopBar 右侧集群窄档折叠（#1667）', () => {
  afterEach(() => {
    delete (window as unknown as { matchMedia?: unknown }).matchMedia;
  });

  it('390px：「设置」内联可达 —— 点击调 setSettingsOpen(true)（验收 1 命中入口）', async () => {
    installMatchMedia(390);
    const user = userEvent.setup();
    render(<TopBar />);

    const settings = screen.getByRole('button', { name: '设置' });
    expect(settings).toBeInTheDocument();

    await user.click(settings);
    expect(mockSetSettingsOpen).toHaveBeenCalledWith(true);
  });

  it('390px：宽控件（历史/底图/3D/UI 调整）不内联，收进「更多」收纳菜单', async () => {
    installMatchMedia(390);
    const user = userEvent.setup();
    render(<TopBar />);

    // 收纳前：宽控件不在 DOM —— 内联集群只剩 新建 / 更多 / 设置 三个 26px 按钮。
    expect(screen.queryByTestId('baselayer-switcher')).toBeNull();
    expect(screen.queryByRole('button', { name: '历史记录' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'UI 调整' })).toBeNull();
    expect(screen.queryByRole('button', { name: /切换至/ })).toBeNull();
    // 结构预算：内联操作按钮 ≤ 4（展开侧栏 + 新建 + 更多 + 设置）——390px 下
    // 集群固有宽度 ~130px，顶栏不再溢出（像素断言见 j8 e2e）。
    const actions = screen.getByTestId('topbar-actions');
    expect(within(actions).getAllByRole('button')).toHaveLength(3);

    const more = screen.getByRole('button', { name: '更多操作' });
    expect(more).toHaveAttribute('aria-expanded', 'false');
    await user.click(more);
    expect(more).toHaveAttribute('aria-expanded', 'true');

    const menu = screen.getByRole('menu', { name: '更多操作' });
    expect(within(menu).getByRole('menuitem', { name: '历史记录' })).toBeInTheDocument();
    expect(within(menu).getByRole('menuitem', { name: 'UI 调整' })).toBeInTheDocument();
    expect(within(menu).getByRole('menuitem', { name: '切换至 3D 视角' })).toBeInTheDocument();
    expect(within(menu).getByTestId('baselayer-switcher')).toBeInTheDocument();
  });

  it('390px：「更多」内 历史/UI 调整 点击后走同一 store 动作并关菜单', async () => {
    installMatchMedia(390);
    const user = userEvent.setup();
    render(<TopBar />);

    await user.click(screen.getByRole('button', { name: '更多操作' }));
    await user.click(screen.getByRole('menuitem', { name: '历史记录' }));
    expect(mockSetHistoryOpen).toHaveBeenCalledWith(true);
    expect(screen.queryByRole('menu', { name: '更多操作' })).toBeNull();

    await user.click(screen.getByRole('button', { name: '更多操作' }));
    await user.click(screen.getByRole('menuitem', { name: 'UI 调整' }));
    expect(mockSetTweaksOpen).toHaveBeenCalledWith(true);
    expect(screen.queryByRole('menu', { name: '更多操作' })).toBeNull();
  });

  it('390px：Escape 关闭「更多」菜单', async () => {
    installMatchMedia(390);
    const user = userEvent.setup();
    render(<TopBar />);

    await user.click(screen.getByRole('button', { name: '更多操作' }));
    expect(screen.getByRole('menu', { name: '更多操作' })).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu', { name: '更多操作' })).toBeNull();
  });

  it('宽档（1440px）：右侧集群维持全内联布局（折叠不回归）', () => {
    installMatchMedia(1440);
    render(<TopBar />);

    expect(screen.queryByRole('button', { name: '更多操作' })).toBeNull();
    expect(screen.getByRole('button', { name: '历史记录' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'UI 调整' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '设置' })).toBeInTheDocument();
    expect(screen.getByTestId('baselayer-switcher')).toBeInTheDocument();
    // 内联集群：新建 + 历史 + 3D + UI 调整 + 设置（底图开关为受 mock 的 div）。
    expect(within(screen.getByTestId('topbar-actions')).getAllByRole('button')).toHaveLength(5);
  });
});