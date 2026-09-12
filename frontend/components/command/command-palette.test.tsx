import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CommandPalette } from './command-palette';
import { registerCommands, resetRegistryForTests } from '@/lib/commands/registry';
import { useCommandPaletteStore } from '@/lib/hooks/use-command-palette';
import type { CommandDef } from '@/lib/commands/types';

function cmd(overrides: Partial<CommandDef> & { id: string }): CommandDef {
  return { title: overrides.id, group: '测试', run: vi.fn(), ...overrides };
}

beforeEach(() => {
  resetRegistryForTests();
  useCommandPaletteStore.getState().close();
  vi.mocked(localStorage.getItem).mockReset();
  vi.mocked(localStorage.getItem).mockReturnValue(null);
});

describe('CommandPalette（APG combobox）', () => {
  it('打开后渲染对话框并聚焦输入框', async () => {
    registerCommands([cmd({ id: 'a.b', title: '导出 PNG' })]);
    render(<CommandPalette />);
    expect(screen.queryByRole('dialog')).toBeNull();
    act(() => useCommandPaletteStore.getState().open('palette'));
    const dialog = await screen.findByRole('dialog', { name: '命令面板' });
    expect(dialog).toBeInTheDocument();
    const input = screen.getByRole('combobox', { name: '搜索命令' });
    await waitFor(() => expect(input).toHaveFocus());
  });

  it('查询过滤 + 分组渲染', async () => {
    registerCommands([
      cmd({ id: 'a.b', title: '导出 PNG', group: '文件' }),
      cmd({ id: 'a.c', title: '导出 PDF', group: '文件' }),
      cmd({ id: 'n.s', title: '新建会话', group: '会话' }),
    ]);
    render(<CommandPalette />);
    act(() => useCommandPaletteStore.getState().open('palette'));
    const input = await screen.findByRole('combobox', { name: '搜索命令' });
    fireEvent.change(input, { target: { value: '导出' } });
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(2));
    expect(screen.getByText('文件')).toBeInTheDocument();
    expect(screen.queryByText('新建会话')).toBeNull();
  });

  it('when 谓词为 false 的命令不渲染', () => {
    registerCommands([
      cmd({ id: 'gated', title: ' gated', when: () => false }),
      cmd({ id: 'free', title: 'free 命令' }),
    ]);
    render(<CommandPalette />);
    act(() => useCommandPaletteStore.getState().open('palette'));
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent('free 命令');
  });

  it('ArrowDown/Up/Home/End 全导航 + Enter 执行并关闭', async () => {
    const run = vi.fn();
    registerCommands([
      cmd({ id: 'one', title: '甲' }),
      cmd({ id: 'two', title: '乙', run }),
      cmd({ id: 'three', title: '丙' }),
    ]);
    render(<CommandPalette />);
    act(() => useCommandPaletteStore.getState().open('palette'));
    const input = await screen.findByRole('combobox', { name: '搜索命令' });
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(3));

    const activeId = () => input.getAttribute('aria-activedescendant');
    const optionText = () =>
      document.getElementById(activeId() ?? '')?.textContent ?? '';

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(optionText()).toContain('乙');
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(optionText()).toContain('丙');
    fireEvent.keyDown(input, { key: 'ArrowDown' }); // 循环回甲
    expect(optionText()).toContain('甲');
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(optionText()).toContain('丙');
    fireEvent.keyDown(input, { key: 'Home' });
    expect(optionText()).toContain('甲');
    fireEvent.keyDown(input, { key: 'End' });
    expect(optionText()).toContain('丙');

    // Home → 甲，ArrowDown → 乙，Enter 执行乙（End 后活动项停在丙，直接
    // Enter 会执行丙的 run —— 故先归位再步进到目标项）
    fireEvent.keyDown(input, { key: 'Home' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][0]).toMatchObject({ source: 'palette' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(useCommandPaletteStore.getState().surface).toBeNull();
    // 最近使用已写入 localStorage（LRU 回读语义在 registry.test.ts 覆盖）
    const setCalls = vi.mocked(localStorage.setItem).mock.calls;
    expect(setCalls.some(([, v]) => String(v).includes('two'))).toBe(true);
  });

  it('Escape 关闭（useDialogFocus 路径）', async () => {
    registerCommands([cmd({ id: 'a', title: '甲' })]);
    render(<CommandPalette />);
    act(() => useCommandPaletteStore.getState().open('palette'));
    const input = await screen.findByRole('combobox', { name: '搜索命令' });
    // 键事件从容器内元素冒泡到 document 级监听（与真实焦点路径一致）
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(useCommandPaletteStore.getState().surface).toBeNull();
  });

  it('参数化命令：Enter 进入参数模式，二次 Enter 带输入执行；Esc 退出参数模式不关面板', async () => {
    const run = vi.fn();
    registerCommands([
      cmd({ id: 'p.cmd', title: '带参命令', run, paramSpec: { prompt: '输入图层名' } }),
    ]);
    render(<CommandPalette />);
    act(() => useCommandPaletteStore.getState().open('palette'));
    const input = await screen.findByRole('combobox', { name: '搜索命令' });
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(1));

    fireEvent.keyDown(input, { key: 'Enter' });
    expect(run).not.toHaveBeenCalled();
    expect(screen.getByRole('combobox', { name: '命令参数' })).toBeInTheDocument();
    expect(screen.getByText('输入图层名')).toBeInTheDocument();

    // Esc 先退出参数模式，面板保持打开（键事件从参数输入框冒泡到 document）
    fireEvent.keyDown(screen.getByRole('combobox', { name: '命令参数' }), { key: 'Escape' });
    expect(useCommandPaletteStore.getState().surface).toBe('palette');
    expect(screen.getByRole('combobox', { name: '搜索命令' })).toBeInTheDocument();

    // 再次进入参数模式，输入后 Enter 带参执行
    fireEvent.keyDown(screen.getByRole('combobox', { name: '搜索命令' }), { key: 'Enter' });
    const paramInput = screen.getByRole('combobox', { name: '命令参数' });
    fireEvent.change(paramInput, { target: { value: 'dem 层' } });
    fireEvent.keyDown(paramInput, { key: 'Enter' });
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][0]).toMatchObject({ source: 'palette' });
    expect(run.mock.calls[0][1]).toBe('dem 层');
    await waitFor(() => expect(useCommandPaletteStore.getState().surface).toBeNull());
  });

  it('空 query 展示最近使用合成组', () => {
    vi.mocked(localStorage.getItem).mockReturnValue(JSON.stringify(['n.s']));
    registerCommands([
      cmd({ id: 'n.s', title: '新建会话', group: '会话' }),
      cmd({ id: 'a.b', title: '导出 PNG', group: '文件' }),
    ]);
    render(<CommandPalette />);
    act(() => useCommandPaletteStore.getState().open('palette'));
    expect(screen.getByText('最近使用')).toBeInTheDocument();
    const firstOption = screen.getAllByRole('option')[0];
    expect(firstOption).toHaveTextContent('新建会话');
  });

  it('aria-live 播报当前活动项', async () => {
    registerCommands([cmd({ id: 'one', title: '甲' }), cmd({ id: 'two', title: '乙' })]);
    render(<CommandPalette />);
    act(() => useCommandPaletteStore.getState().open('palette'));
    const input = await screen.findByRole('combobox', { name: '搜索命令' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    const announcer = screen.getByTestId('command-palette-announcer');
    await waitFor(() => expect(announcer).toHaveTextContent('第 2 项，共 2 项：乙'));
  });
});
