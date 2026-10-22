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

describe('fuzzy search 性能契约：5000 条 <32ms', () => {
  // 原契约为「单查询均值 <16ms」，在共享 CI runner 上被负载方差击穿：
  // #1598（run 37591222612）实测均值 21.5ms、超阈值 ~35%，同码 24h 内一绿一红，
  // 判定 runner 负载方差 flake 而非代码回归。两处调整：
  // 1) 阈值 16ms → 32ms（=2× 原预算，对实测争抢值 21.5ms 留 ~49% 余量），
  //    保留量级契约语义——O(n²) 级退化会同时抬升 median 与 p95 一个数量级，
  //    仍被本断言捕获（本机 median ~1.7ms，低于新阈值 ~19×）。
  // 2) 统计量从均值改为 median + p95：单轮调度/GC 尖峰只污染极端分位，
  //    不再像均值那样被单轮抖动整体拉爆。
  it('5000 命令单查询 median <32ms 且 p95 <48ms', () => {
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
    const runs = 20;
    const perQuery: number[] = [];
    for (let r = 0; r < runs; r++) {
      const t0 = performance.now();
      for (const q of queries) searchIndexed(index, q);
      perQuery.push((performance.now() - t0) / queries.length);
    }
    const sorted = [...perQuery].sort((a, b) => a - b);
    const median = sorted[Math.floor(sorted.length / 2)];
    // nearest-rank p95：20 样本下即次大值，容忍单轮尾部尖峰
    const p95 = sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1)];
    expect(median).toBeLessThan(32);
    // p95 放宽到 1.5× median 预算：吸收共享 runner 的调度/GC 尖峰；
    // 真实退化会同时抬高 median 与 p95，两个断言一起失效
    expect(p95).toBeLessThan(48);
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
