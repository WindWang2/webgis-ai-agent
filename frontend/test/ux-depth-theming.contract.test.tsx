/**
 * 本线新表面的明/暗双主题渲染契约（P7 visual snapshot 惯例对齐）。
 *
 * 仓库无像素 diff 服务（P0 勘察 §4）：既有惯例 = design-system token/对比度
 * 契约测试 + capture.mjs 采集器。本测试补齐「同一表面在 light/dark 两个
 * data-theme + dark class 下均完整渲染且壳层走语义 token」的组件级契约——
 * 主题切换经 <html data-theme> + .dark 双写（与 app/page.tsx 同机制）。
 */
import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { CommandPalette } from '@/components/command/command-palette';
import { ShortcutOverview } from '@/components/command/shortcut-overview';
import { UndoHistoryPanel } from '@/components/workbench/undo-history-panel';
import { HintQueue } from '@/components/onboarding/hint-queue';
import { registerCommands, resetRegistryForTests } from '@/lib/commands/registry';
import { useCommandPaletteStore } from '@/lib/hooks/use-command-palette';
import { useUndoHistoryStore } from '@/lib/hooks/use-undo-history';
import { useOnboardingStore } from '@/lib/onboarding/use-onboarding';
import { HINTS } from '@/components/onboarding/tour-steps';

beforeEach(() => {
  resetRegistryForTests();
  registerCommands([
    { id: 'a', title: '命令甲', group: '测试', shortcut: 'mod+z', run: () => {} },
    { id: 'b', title: '命令乙', group: '测试', run: () => {} },
  ]);
  useCommandPaletteStore.getState().close();
  useUndoHistoryStore.getState().closePanel();
  act(() => {
    useOnboardingStore.setState({
      tourSeen: true,
      tourOpen: false,
      hintsSeen: [],
      activeHintId: HINTS[0].id,
    });
  });
});

function applyTheme(theme: 'light' | 'dark'): void {
  document.documentElement.classList.toggle('dark', theme === 'dark');
  document.documentElement.setAttribute('data-theme', theme);
}

/** 壳层必须走语义表面 token（暗色可反转），不允许硬编码色。 */
function expectSemanticShell(el: HTMLElement): void {
  expect(el).toHaveClass('bg-surface-raised');
  expect(el.className).not.toMatch(/(bg|text)-(gray|slate|zinc)-\d/);
}

describe('双主题渲染契约（light/dark × 新表面）', () => {
  for (const theme of ['light', 'dark'] as const) {
    it(`${theme}：命令面板`, () => {
      applyTheme(theme);
      render(<CommandPalette />);
      act(() => useCommandPaletteStore.getState().open('palette'));
      expectSemanticShell(screen.getByTestId('command-palette'));
      expect(screen.getAllByRole('option').length).toBeGreaterThan(0);
    });

    it(`${theme}：快捷键总览`, () => {
      applyTheme(theme);
      render(<ShortcutOverview />);
      act(() => useCommandPaletteStore.getState().open('shortcuts'));
      expectSemanticShell(screen.getByTestId('shortcut-overview'));
      expect(screen.getAllByTestId('shortcut-row').length).toBeGreaterThan(0);
    });

    it(`${theme}：操作历史 + 提示卡`, () => {
      applyTheme(theme);
      render(
        <>
          <UndoHistoryPanel />
          <HintQueue />
        </>,
      );
      act(() => useUndoHistoryStore.getState().openPanel());
      expectSemanticShell(screen.getByTestId('undo-history'));
      expect(screen.getByTestId('onboarding-hint')).toBeInTheDocument();
    });
  }
});
