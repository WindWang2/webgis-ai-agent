import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  detectShortcutConflicts,
  getCommandById,
  getRecentCommandIds,
  getAllCommands,
  recordRecentCommand,
  registerCommands,
  resetRegistryForTests,
} from './registry';
import type { CommandDef } from './types';

function cmd(overrides: Partial<CommandDef> & { id: string }): CommandDef {
  return {
    title: overrides.id,
    group: '测试',
    run: vi.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  resetRegistryForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('command registry', () => {
  it('注册后可通过 id 与全量列表取回', () => {
    const unregister = registerCommands([cmd({ id: 'a.b' }), cmd({ id: 'a.c' })]);
    expect(getCommandById('a.b')?.id).toBe('a.b');
    expect(getAllCommands().map((c) => c.id).sort()).toEqual(['a.b', 'a.c']);
    unregister();
    expect(getCommandById('a.b')).toBeUndefined();
    expect(getAllCommands()).toHaveLength(0);
  });

  it('重复 id：后注册者被忽略（append-only 契约）', () => {
    const first = registerCommands([cmd({ id: 'x', title: 'first' })]);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const second = registerCommands([cmd({ id: 'x', title: 'second' })]);
    expect(getCommandById('x')?.title).toBe('first');
    second(); // 反注册不得误删先注册者
    expect(getCommandById('x')?.title).toBe('first');
    first();
    expect(getCommandById('x')).toBeUndefined();
    expect(warn).toHaveBeenCalled();
  });

  it('反注册幂等', () => {
    const unregister = registerCommands([cmd({ id: 'a' })]);
    unregister();
    expect(() => unregister()).not.toThrow();
    expect(getAllCommands()).toHaveLength(0);
  });

  it('冲突检测：同归一化快捷键 ≥2 条命令', () => {
    registerCommands([
      cmd({ id: 'one', shortcut: 'Ctrl+K' }),
      cmd({ id: 'two', shortcut: 'mod+k' }),
      cmd({ id: 'three', shortcut: 'mod+z' }),
    ]);
    const conflicts = detectShortcutConflicts();
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0].commandIds.sort()).toEqual(['one', 'two']);
  });

  it('无冲突时返回空数组', () => {
    registerCommands([cmd({ id: 'a', shortcut: 'mod+z' }), cmd({ id: 'b', shortcut: '?' })]);
    expect(detectShortcutConflicts()).toEqual([]);
  });

  it('when 谓词由消费方求值，注册表原样保存', () => {
    const when = vi.fn(() => false);
    registerCommands([cmd({ id: 'gated', when })]);
    expect(getCommandById('gated')?.when).toBe(when);
  });
});

describe('recent commands (LRU ≤8, localStorage)', () => {
  beforeEach(() => {
    vi.mocked(localStorage.getItem).mockReset();
    vi.mocked(localStorage.setItem).mockReset();
  });

  it('空存储返回空列表', () => {
    vi.mocked(localStorage.getItem).mockReturnValue(null);
    expect(getRecentCommandIds()).toEqual([]);
  });

  it('记录去重置顶并截断到 8', () => {
    let stored: string | null = null;
    vi.mocked(localStorage.getItem).mockImplementation(() => stored);
    vi.mocked(localStorage.setItem).mockImplementation((_k, v) => {
      stored = v as string;
    });
    for (let i = 0; i < 10; i++) recordRecentCommand(`cmd-${i}`);
    recordRecentCommand('cmd-3');
    const ids = getRecentCommandIds();
    expect(ids).toHaveLength(8);
    expect(ids[0]).toBe('cmd-3');
    // 置顶 cmd-3 后挤掉的是队尾 cmd-1 / cmd-0
    expect(ids).toContain('cmd-9');
    expect(ids).not.toContain('cmd-1');
    expect(ids).not.toContain('cmd-0');
  });

  it('损坏的存储内容安全降级为空', () => {
    vi.mocked(localStorage.getItem).mockReturnValue('not-json{{');
    expect(getRecentCommandIds()).toEqual([]);
  });
});
