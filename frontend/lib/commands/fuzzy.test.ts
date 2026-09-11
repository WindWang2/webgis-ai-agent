import { describe, expect, it } from 'vitest';
import { buildSearchIndex, fuzzyMatch, searchIndexed } from './fuzzy';
import { formatShortcut, isEditableTarget, matchesShortcut, normalizeShortcut } from './shortcut';

describe('fuzzyMatch', () => {
  it('空 query 恒命中', () => {
    expect(fuzzyMatch('', 'anything')).toEqual({ score: 0, indices: [] });
  });

  it('子序列命中并返回下标', () => {
    const m = fuzzyMatch('pt', 'export png');
    expect(m).not.toBeNull();
    expect(m!.indices).toEqual([2, 5]);
  });

  it('query 长于 haystack 不命中', () => {
    expect(fuzzyMatch('abcdef', 'abc')).toBeNull();
  });

  it('顺序错乱不命中（子序列语义）', () => {
    expect(fuzzyMatch('px', 'png export')).not.toBeNull(); // p..x 存在
    expect(fuzzyMatch('xn', 'png export')).toBeNull();
  });

  it('词首命中得分高于词中', () => {
    const wordStart = fuzzyMatch('e', 'png export')!;
    const mid = fuzzyMatch('e', 'queue')!;
    expect(wordStart.score).toBeGreaterThan(mid.score);
  });

  it('前缀命中得分最高', () => {
    const prefix = fuzzyMatch('ex', 'export png')!;
    const mid = fuzzyMatch('ex', 'png export')!;
    expect(prefix.score).toBeGreaterThan(mid.score);
  });
});

describe('searchIndexed', () => {
  const index = buildSearchIndex([
    { id: 'a', title: '导出 PNG', group: '文件' },
    { id: 'b', title: '导出 PDF', group: '文件', keywords: 'portable document' },
    { id: 'c', title: '新建会话', group: '会话' },
  ]);

  it('空 query 返回全部（稳定序）', () => {
    expect(searchIndexed(index, '').map((h) => h.id)).toEqual(['a', 'b', 'c']);
  });

  it('title 命中优先于 keywords', () => {
    const hits = searchIndexed(index, '导出');
    expect(hits.map((h) => h.id)).toEqual(['a', 'b']);
  });

  it('keywords 兜底命中', () => {
    expect(searchIndexed(index, 'document').map((h) => h.id)).toEqual(['b']);
  });

  it('无命中返回空', () => {
    expect(searchIndexed(index, 'zzzz')).toEqual([]);
  });
});

describe('fuzzy search 性能契约：5000 条 <16ms', () => {
  it('5000 命令单查询 <16ms（均值）', () => {
    const items = Array.from({ length: 5000 }, (_, i) => ({
      id: `cmd-${i}`,
      title: `命令 ${i} 操作项目${i % 97}`,
      group: `组${i % 20}`,
      keywords: `kw ${i} alpha beta gamma`,
    }));
    const index = buildSearchIndex(items);
    const queries = ['命令', 'cmd', 'alpha', '操作项', '组1', 'beta ga', 'zzz', '0'];
    // 预热（JIT + 缓存效应不计入断言）
    for (const q of queries) searchIndexed(index, q);
    const t0 = performance.now();
    const runs = 20;
    for (let r = 0; r < runs; r++) {
      for (const q of queries) searchIndexed(index, q);
    }
    const perQuery = (performance.now() - t0) / (runs * queries.length);
    expect(perQuery).toBeLessThan(16);
  });
});

describe('shortcut utils', () => {
  it('normalizeShortcut：修饰符排序 + 小写', () => {
    expect(normalizeShortcut('Ctrl+K')).toBe('ctrl+k');
    expect(normalizeShortcut('Shift+Ctrl+P')).toBe('ctrl+shift+p');
  });

  const keyEvent = (over: Partial<KeyboardEvent>): KeyboardEvent =>
    ({
      key: 'k',
      ctrlKey: false,
      altKey: false,
      shiftKey: false,
      metaKey: false,
      ...over,
    }) as KeyboardEvent;

  it('matchesShortcut：显式 ctrl 修饰符', () => {
    expect(matchesShortcut(keyEvent({ ctrlKey: true }), normalizeShortcut('ctrl+k'))).toBe(true);
    expect(matchesShortcut(keyEvent({}), normalizeShortcut('ctrl+k'))).toBe(false);
  });

  it('matchesShortcut：mod 折算为当前平台修饰符（jsdom=非 mac→ctrl）', () => {
    const mac = /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent || '');
    const e = keyEvent(mac ? { metaKey: true } : { ctrlKey: true });
    expect(matchesShortcut(e, normalizeShortcut('mod+k'))).toBe(true);
    expect(matchesShortcut(keyEvent({ metaKey: true, ctrlKey: true }), normalizeShortcut('mod+k'))).toBe(false);
  });

  it('matchesShortcut：带 shift 的字母键严格比对 shift', () => {
    const shiftZ = keyEvent({ key: 'Z', ctrlKey: true, shiftKey: true });
    expect(matchesShortcut(shiftZ, normalizeShortcut('mod+shift+z'))).toBe(true);
    expect(matchesShortcut(shiftZ, normalizeShortcut('mod+z'))).toBe(false);
  });

  it('matchesShortcut：`?` 符号键（shift 参与 key 形变）', () => {
    const q = { key: '?', ctrlKey: false, altKey: false, shiftKey: true, metaKey: false } as KeyboardEvent;
    expect(matchesShortcut(q, normalizeShortcut('?'))).toBe(true);
  });

  it('formatShortcut：非 mac 平台 Ctrl 表示', () => {
    expect(formatShortcut('mod+k')).toMatch(/^(Ctrl|⌘)\+?K$/);
    expect(formatShortcut('ctrl+shift+p')).toMatch(/Ctrl\+Shift\+P/);
  });

  it('isEditableTarget 识别输入元素与 contenteditable', () => {
    const input = document.createElement('input');
    const div = document.createElement('div');
    // jsdom 不把 contentEditable 属性赋值反射到 attribute，浏览器里两者等价
    div.setAttribute('contenteditable', 'true');
    expect(isEditableTarget(input)).toBe(true);
    expect(isEditableTarget(div)).toBe(true);
    expect(isEditableTarget(document.body)).toBe(false);
    expect(isEditableTarget(null)).toBe(false);
  });
});
