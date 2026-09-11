import { beforeEach, describe, expect, it } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { ShortcutOverview } from './shortcut-overview';
import { registerCommands, resetRegistryForTests } from '@/lib/commands/registry';
import { useCommandPaletteStore } from '@/lib/hooks/use-command-palette';
import type { CommandDef } from '@/lib/commands/types';

function cmd(overrides: Partial<CommandDef> & { id: string }): CommandDef {
  return { title: overrides.id, group: '测试', run: () => {}, ...overrides };
}

beforeEach(() => {
  resetRegistryForTests();
  useCommandPaletteStore.getState().close();
});

describe('ShortcutOverview', () => {
  it('关闭时不渲染', () => {
    render(<ShortcutOverview />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('列出全部带快捷键的命令并按组展示；无快捷键命令不列', () => {
    registerCommands([
      cmd({ id: 'a', title: '有键甲', group: '编辑', shortcut: 'mod+z' }),
      cmd({ id: 'b', title: '有键乙', group: '编辑', shortcut: 'mod+shift+z' }),
      cmd({ id: 'c', title: '无键丙', group: '编辑' }),
    ]);
    render(<ShortcutOverview />);
    act(() => useCommandPaletteStore.getState().open('shortcuts'));
    expect(screen.getByRole('dialog', { name: '快捷键总览' })).toBeInTheDocument();
    expect(screen.getByText('有键甲')).toBeInTheDocument();
    expect(screen.getByText('有键乙')).toBeInTheDocument();
    expect(screen.queryByText('无键丙')).toBeNull();
    expect(screen.getAllByTestId('shortcut-group').map((g) => g.querySelector('h3')?.textContent)).toContain('编辑');
  });

  it('同键多命令时展示冲突警示（role=alert）', () => {
    registerCommands([
      cmd({ id: 'one', title: '冲突一', group: '编辑', shortcut: 'ctrl+k' }),
      cmd({ id: 'two', title: '冲突二', group: '视图', shortcut: 'Ctrl+K' }),
    ]);
    render(<ShortcutOverview />);
    act(() => useCommandPaletteStore.getState().open('shortcuts'));
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('检测到 1 处快捷键冲突');
    expect(alert).toHaveTextContent('冲突一');
    expect(alert).toHaveTextContent('冲突二');
  });

  it('无冲突时无警示块', () => {
    registerCommands([cmd({ id: 'a', title: '甲', shortcut: 'mod+z' })]);
    render(<ShortcutOverview />);
    act(() => useCommandPaletteStore.getState().open('shortcuts'));
    expect(screen.queryByTestId('shortcut-conflicts')).toBeNull();
  });

  it('when=false 的命令不参与总览与冲突检测', () => {
    registerCommands([
      cmd({ id: 'g1', title: '隐藏甲', shortcut: 'ctrl+k', when: () => false }),
      cmd({ id: 'g2', title: '隐藏乙', shortcut: 'ctrl+k', when: () => false }),
      cmd({ id: 'ok', title: '可见', group: '编辑', shortcut: 'mod+z' }),
    ]);
    render(<ShortcutOverview />);
    act(() => useCommandPaletteStore.getState().open('shortcuts'));
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText('隐藏甲')).toBeNull();
    expect(screen.getByText('可见')).toBeInTheDocument();
  });
});
