import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useState } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { SessionSummary } from '@/lib/store/hud-types';

// history-drawer 从 useHudStore 读取 sessions。提供一个可预测的 session 列表，
// 使面板内可聚焦元素（搜索框 + 两个按钮 + 会话项按钮）顺序固定。
const SESSIONS: SessionSummary[] = [
  { id: 's1', title: '会话一', time: '今天', msgs: 3, tags: [] },
  { id: 's2', title: '会话二', time: '昨天', msgs: 1, tags: [] },
];

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: { sessions: SessionSummary[] }) => unknown) =>
    selector({ sessions: SESSIONS }),
}));

import { HistoryDrawer } from './history-drawer';

describe('HistoryDrawer keyboard focus trap', () => {
  // 审计 a11y HIGH（findings.md F4 残留缺口）：drawer 已有 role/aria-modal/Escape/
  // 打开聚焦/关闭回焦，但缺 Tab 焦点陷阱 —— 键盘用户可 Tab 到背景元素。
  // 该用例锁定：打开 drawer 后，Tab/Shift+Tab 在面板内循环，焦点不出面板。
  beforeEach(() => {
    // jsdom 默认不会在 Tab 时移动焦点；user-event 需要 document 处于「已聚焦」状态。
    document.hasFocus = () => true;
  });

  it('traps Tab inside the drawer — wraps from last to first focusable element', async () => {
    const user = userEvent.setup();
    render(
      <HistoryDrawer
        open
        onClose={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    const panel = await screen.findByRole('dialog');
    await waitForInitialFocus();
    const focusables = getTabbable(panel);
    expect(focusables.length).toBeGreaterThanOrEqual(2);

    // 移到面板内最后一个可聚焦元素。
    focusables[focusables.length - 1].focus();
    expect(document.activeElement).toBe(focusables[focusables.length - 1]);

    // 在最后一个元素上按 Tab → 应回到第一个（循环），而非离开面板。
    await user.tab();

    expect(document.activeElement).toBe(focusables[0]);
  });

  it('traps Shift+Tab inside the drawer — wraps from first to last focusable element', async () => {
    const user = userEvent.setup();
    render(
      <HistoryDrawer
        open
        onClose={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    const panel = await screen.findByRole('dialog');
    await waitForInitialFocus();
    const focusables = getTabbable(panel);

    focusables[0].focus();
    expect(document.activeElement).toBe(focusables[0]);

    // 在第一个元素上按 Shift+Tab → 应回到最后一个（反向循环）。
    await user.tab({ shift: true });

    expect(document.activeElement).toBe(focusables[focusables.length - 1]);
  });

  it('focus stays inside the panel when tabbing through — never lands on a background element', async () => {
    const user = userEvent.setup();
    // 在 drawer 外部放一个按钮，模拟背景可聚焦元素。陷阱生效时焦点不应落在它上面。
    render(
      <div>
        <button>背景按钮</button>
        <HistoryDrawer
          open
          onClose={vi.fn()}
          onSelect={vi.fn()}
        />
      </div>,
    );

    const panel = await screen.findByRole('dialog');
    await waitForInitialFocus();
    const panelFocusables = getTabbable(panel);
    const backgroundButton = screen.getByText('背景按钮');

    // 从面板内开始，连续 Tab 多次，焦点应始终在面板内。
    panelFocusables[0].focus();
    for (let i = 0; i < panelFocusables.length + 2; i++) {
      await user.tab();
      const active = document.activeElement;
      expect(active).not.toBe(backgroundButton);
      expect(panel.contains(active)).toBe(true);
    }
  });

  it('still closes on Escape — trap implementation must not regress the existing handler', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <HistoryDrawer open onClose={onClose} onSelect={vi.fn()} />,
    );

    await screen.findByRole('dialog');
    await user.keyboard('{Escape}');

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('moves focus into the search input when opened', async () => {
    render(
      <HistoryDrawer open onClose={vi.fn()} onSelect={vi.fn()} />,
    );

    const search = await screen.findByLabelText('搜索历史会话');
    // useDialogFocus（与全站其他 dialog 共用的 hook）初始聚焦带 50ms 延迟，
    // 需等待焦点落位；断言本身不变。
    await waitFor(() => expect(document.activeElement).toBe(search));
  });

  it('restores focus to the previously-focused element when closed', async () => {
    function Harness() {
      const [open, setOpen] = useState(true);
      return (
        <>
          <button onClick={() => setOpen(false)}>触发关闭的背景按钮</button>
          <HistoryDrawer
            open={open}
            onClose={() => setOpen(false)}
            onSelect={vi.fn()}
          />
        </>
      );
    }
    const trigger = userEvent.setup();
    render(<Harness />);
    // 先聚焦背景按钮作为「之前焦点」，再关闭 drawer 验证回焦。
    const bg = screen.getByText('触发关闭的背景按钮');
    bg.focus();
    expect(document.activeElement).toBe(bg);
    await trigger.keyboard('{Escape}');
    expect(document.activeElement).toBe(bg);
  });

  it('closes when the backdrop is clicked', async () => {
    const onClose = vi.fn();
    render(
      <HistoryDrawer open onClose={onClose} onSelect={vi.fn()} />,
    );

    await screen.findByRole('dialog');
    // backdrop 是 dialog 之前的兄弟元素，aria-hidden，无可访问名 —— 按容器首个子元素取。
    const backdrop = document.querySelector('[aria-hidden="true"]') as HTMLElement;
    await userEvent.click(backdrop);

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

// 工具：获取容器内 tab 顺序的可聚焦元素（与浏览器 Tab 行为一致）。
// 注意：jsdom 不计算布局，getClientRects() 恒为空，故不能用可见性过滤 —— 这里只按
// 选择器与 DOM 顺序返回，符合浏览器对可见元素的默认 Tab 顺序。
// 选择器必须与生产代码 lib/utils/focus.ts 的 FOCUSABLE_SELECTOR 完全一致
// （含 input[type=hidden] 排除），否则测试不会覆盖生产代码所做的过滤。
function getTabbable(container: HTMLElement): HTMLElement[] {
  const selector =
    'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
  return Array.from(container.querySelectorAll<HTMLElement>(selector));
}

// 共用 hook（useDialogFocus）的初始聚焦带 50ms 延迟：测试手动接管焦点前先等
// 自动聚焦落位，否则延迟聚焦会在测试执行中途触发、与焦点断言竞态
// （全量跑测试时更慢，更容易触发）。
async function waitForInitialFocus() {
  const search = await screen.findByLabelText('搜索历史会话');
  await waitFor(() => expect(document.activeElement).toBe(search));
}
