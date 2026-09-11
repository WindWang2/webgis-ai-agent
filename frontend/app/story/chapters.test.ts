import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  applyOrchestration,
  assignChapterArtifacts,
  collectRefsFromText,
  collectRefsFromValue,
  deriveChapters,
  deriveTitle,
  extractChapterCameras,
  extractFlyTo,
  loadOrchestration,
  saveOrchestration,
  chapterIdFor,
  type ChapterOrchestration,
} from './chapters';

// setup.ts 的 localStorage 是裸 vi.fn —— 这里接线为内存实现供持久化往返测试
const storage = new Map<string, string>();
beforeEach(() => {
  storage.clear();
  vi.mocked(localStorage.getItem).mockImplementation((k: string) => storage.get(k) ?? null);
  vi.mocked(localStorage.setItem).mockImplementation((k: string, v: string) => {
    storage.set(k, v);
  });
  vi.mocked(localStorage.removeItem).mockImplementation((k: string) => {
    storage.delete(k);
  });
});

describe('章节派生与编排（ADR-0147）', () => {
  const messages = [
    { role: 'assistant', content: '## 水系分布概览\n正文…' },
    { role: 'user', content: '放大到下游看看' },
    { role: 'assistant', content: '下游河网密度更高。' },
  ];

  it('1 消息 = 1 章节，标题从标题行/首行派生，user 加前缀', () => {
    const chapters = deriveChapters(messages);
    expect(chapters).toHaveLength(3);
    expect(chapters[0].title).toBe('水系分布概览');
    expect(chapters[1].title).toMatch(/^问：放大到下游看看/);
    expect(chapters[2].title).toBe('下游河网密度更高。');
    expect(chapters[0].id).toBe('ch-0');
  });

  it('无标题行时取首行截断 24 字符', () => {
    expect(deriveTitle('x'.repeat(40), 'assistant')).toHaveLength(24);
    expect(deriveTitle('', 'assistant')).toBe('未命名章节');
  });

  it('编排覆盖层：重命名/隐藏/重排', () => {
    const derived = deriveChapters(messages);
    const orch = {
      version: 1 as const,
      overrides: {
        'ch-0': { title: '开场', visible: false, order: 2 },
        'ch-2': { order: 0 },
      },
    };
    const next = applyOrchestration(derived, orch);
    expect(next.map((c) => c.id)).toEqual(['ch-2', 'ch-1', 'ch-0']);
    expect(next[2].visible).toBe(false);
    expect(next[2].title).toBe('开场');
  });

  it('非法/过期编排安全兜底（版本不符 / id 缺失）', () => {
    const derived = deriveChapters(messages);
    expect(applyOrchestration(derived, null)).toEqual(derived);
    expect(
      applyOrchestration(derived, { version: 99, overrides: {} } as unknown as ChapterOrchestration),
    ).toEqual(derived);
  });

  it('编排持久化往返 + 损坏数据降级', () => {
    saveOrchestration('s1', { version: 1, overrides: { 'ch-0': { visible: false } } });
    expect(loadOrchestration('s1')?.overrides['ch-0']?.visible).toBe(false);
    expect(loadOrchestration('missing')).toBeNull();
    storage.set('geoagent-story-orchestration-v1:s2', '{{{');
    expect(loadOrchestration('s2')).toBeNull();
    expect(loadOrchestration(null)).toBeNull();
  });
});

describe('产物 ref 归属', () => {
  it('文本提取去重', () => {
    expect(collectRefsFromText('见 ref:chart-abc 与 ref:chart-abc、ref:stats-t1')).toEqual([
      'ref:chart-abc',
      'ref:stats-t1',
    ]);
  });

  it('深走 mapstate JSON 收集 ref', () => {
    const state = {
      mapspec: { layers: [{ component: { chart_ref: 'ref:chart-xyz' } }, { tableRef: 'ref:grid-9' }] },
    };
    expect(collectRefsFromValue(state).sort()).toEqual(['ref:chart-xyz', 'ref:grid-9']);
  });

  it('内容提及归本章；mapspec 剩余归末章（附录）', () => {
    const messages = [
      { role: 'assistant', content: '看这张图 ref:chart-a' },
      { role: 'assistant', content: '总结' },
    ];
    const byChapter = assignChapterArtifacts(messages, {
      mapspec: { layers: [{ v: 'ref:chart-b' }] },
    } as never);
    expect(byChapter['ch-0']).toEqual(['ref:chart-a']);
    expect(byChapter['ch-1']).toEqual(['ref:chart-b']);
  });

  it('无任何产物时为空表', () => {
    expect(assignChapterArtifacts([{ role: 'assistant', content: '纯文本' }], null)).toEqual({});
  });
});

describe('章节相机提取（fly_to 围栏）', () => {
  it('从 ```json 围栏解析 fly_to', () => {
    const content = '前言\n```json\n{"command":"fly_to","params":{"center":[116.4,39.9],"zoom":11}}\n```\n后记';
    expect(extractFlyTo(content)).toEqual({ center: [116.4, 39.9], zoom: 11 });
  });

  it('非 fly_to / 非法 JSON / 无围栏 → null', () => {
    expect(extractFlyTo('```json\n{"command":"base_layer_change"}\n```')).toBeNull();
    expect(extractFlyTo('```json\n{broken\n```')).toBeNull();
    expect(extractFlyTo('纯文本')).toBeNull();
    expect(extractFlyTo('')).toBeNull();
  });

  it('extractChapterCameras 按章节 id 索引', () => {
    const cams = extractChapterCameras([
      { content: '```json\n{"command":"fly_to","params":{"center":[1,2]}}\n```' },
      { content: '无相机' },
    ]);
    expect(cams[chapterIdFor(0)]).toEqual({ center: [1, 2], zoom: undefined });
    expect(cams[chapterIdFor(1)]).toBeNull();
  });
});

describe('48 章节长叙事（渲染规模契约）', () => {
  it('48 章节派生 + 编排应用 <16ms（纯逻辑层）', () => {
    const messages = Array.from({ length: 48 }, (_, i) => ({
      role: i % 4 === 1 ? 'user' : 'assistant',
      content: `## 章节 ${i + 1}\n分析推演正文`.padEnd(60, '。'),
    }));
    const t0 = performance.now();
    const derived = deriveChapters(messages);
    const orch = { version: 1 as const, overrides: { 'ch-0': { visible: false } } };
    const applied = applyOrchestration(derived, orch);
    const cams = extractChapterCameras(messages);
    const artifacts = assignChapterArtifacts(messages, null);
    expect(performance.now() - t0).toBeLessThan(16);
    expect(applied).toHaveLength(48);
    expect(applied.find((c) => c.id === 'ch-0')?.visible).toBe(false);
    expect(Object.keys(cams)).toHaveLength(48);
    expect(artifacts).toEqual({});
  });
});

// 防回归占位：chapter-artifact 的 ref 分流（chart vs table）在组件测试覆盖
describe('smoke', () => {
  it('chapterIdFor 稳定', () => {
    expect(chapterIdFor(12)).toBe('ch-12');
  });

  it('localStorage 不可用时 saveOrchestration 不抛', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    expect(() => saveOrchestration('sx', { version: 1, overrides: {} })).not.toThrow();
    spy.mockRestore();
  });
});
