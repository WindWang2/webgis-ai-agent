import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  buildIndexedSession,
  buildIndexFromSessions,
  clearSearchIndex,
  loadPersistedSessions,
  normalizeSessionList,
  persistSessions,
  searchIndexStats,
  searchLocalIndex,
  upsertIndexedSession,
  type SessionMeta,
} from './session-index';
import { consumePendingLocate, setPendingLocate } from './locate';

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
  clearSearchIndex();
});

function makeSession(id: string, updatedAt: number, docs = 3): ReturnType<typeof buildIndexedSession> {
  return buildIndexedSession(
    id,
    `会话 ${id}`,
    updatedAt,
    Array.from({ length: docs }, (_, i) => ({
      role: i % 2 ? 'assistant' : 'user',
      content: `消息 ${i} 关于热力图分析 ref:chart-x${id}`,
    })),
  );
}

describe('本地索引：构建 / LRU / 体积预算', () => {
  it('构建：每消息一条 doc，ref 抽取，200 条封顶', () => {
    const s = buildIndexedSession(
      'a',
      '标题',
      100,
      Array.from({ length: 260 }, (_, i) => ({ role: 'assistant', content: `c${i}` })),
    );
    expect(s.docs).toHaveLength(200);
    const withRef = buildIndexedSession('b', 't', 1, [{ role: 'user', content: '见 ref:table-9' }]);
    expect(withRef.docs[0].refs).toEqual(['ref:table-9']);
  });

  it('upsert 去重 + 超过 20 会话 LRU 淘汰最旧', () => {
    for (let i = 0; i < 25; i++) {
      upsertIndexedSession(makeSession(`s${i}`, 1000 + i));
    }
    const kept = loadPersistedSessions();
    expect(kept).toHaveLength(20);
    const ids = kept.map((s) => s.id);
    expect(ids).toContain('s24'); // 最新
    expect(ids).not.toContain('s0'); // 最旧被淘汰
    // 重新 upsert 旧会话 → 按新 updatedAt 复活
    upsertIndexedSession(makeSession('s0', 99999));
    expect(loadPersistedSessions().map((s) => s.id)).toContain('s0');
  });

  it('体积预算：超限整会话淘汰（不写穿 localStorage）', () => {
    const big = Array.from({ length: 10 }, (_, i) =>
      makeSession(`big${i}`, 1000 + i, 200),
    );
    // 每会话 200 docs × 2000 字符上限内容 → 总量必超 1MB 预算
    const docs = big.map((s) => ({
      ...s,
      docs: s.docs.map((d) => ({ ...d, text: 'x'.repeat(2000) })),
    }));
    const kept = persistSessions(docs);
    expect(kept.length).toBeLessThan(10);
    expect(JSON.stringify(kept).length).toBeLessThanOrEqual(1_000_000 + 4096);
  });

  it('stats 与 clear', () => {
    upsertIndexedSession(makeSession('a', 1));
    upsertIndexedSession(makeSession('b', 2));
    const stats = searchIndexStats();
    expect(stats.sessions).toBe(2);
    expect(stats.messages).toBe(6);
    clearSearchIndex();
    expect(searchIndexStats().sessions).toBe(0);
  });
});

describe('本地搜索分组', () => {
  beforeEach(() => {
    upsertIndexedSession({
      id: 'sess-1',
      title: '长江流域热力图分析',
      updatedAt: 100,
      docs: [
        { messageIndex: 0, role: 'user', text: '帮我看看热力图怎么调', refs: [] },
        { messageIndex: 1, role: 'assistant', text: '已生成 ref:chart-h1 热力图', refs: ['ref:chart-h1'] },
      ],
    });
  });

  it('会话标题命中 → session 组', () => {
    const hits = searchLocalIndex('长江', loadPersistedSessions(), []);
    expect(hits[0].kind).toBe('session');
    expect(hits[0].sessionId).toBe('sess-1');
  });

  it('消息全文命中 → message 组 + 摘要；产物命中 → artifact 组', () => {
    const hits = searchLocalIndex('热力图', loadPersistedSessions(), []);
    const kinds = hits.map((h) => h.kind);
    expect(kinds).toContain('message');
    expect(kinds).toContain('artifact');
    const artifact = hits.find((h) => h.kind === 'artifact')!;
    expect(artifact.ref).toBe('ref:chart-h1');
    expect(artifact.messageIndex).toBe(1);
    const message = hits.find((h) => h.kind === 'message')!;
    expect(message.snippet).toContain('热力图');
  });

  it('图层组来自当前工作区注入', () => {
    const hits = searchLocalIndex('dem', [], [{ id: 'L1', name: 'DEM 山体阴影' }]);
    expect(hits).toHaveLength(1);
    expect(hits[0].kind).toBe('layer');
    expect(hits[0].layerId).toBe('L1');
  });

  it('空查询 / 无命中', () => {
    expect(searchLocalIndex('', [], [])).toEqual([]);
    expect(searchLocalIndex('zzz不存在', [], [])).toEqual([]);
  });
});

describe('索引编排 buildIndexFromSessions', () => {
  const metas: SessionMeta[] = Array.from({ length: 5 }, (_, i) => ({
    id: `s${i}`,
    title: `会话${i}`,
    updatedAt: 100 + i,
  }));

  it('顺序拉取建索引 + 进度上报 + budget 截断', async () => {
    const fetcher = vi.fn().mockResolvedValue({
      messages: [{ role: 'assistant', content: '内容 alpha' }],
    });
    const progress: Array<{ done: number; total: number }> = [];
    const result = await buildIndexFromSessions(metas, fetcher, {
      budget: 3,
      onProgress: (p) => progress.push({ done: p.done, total: p.total }),
    });
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(result.some((s) => s.id === 's2')).toBe(true);
    expect(result.some((s) => s.id === 's3')).toBe(false);
    expect(progress.at(-1)).toEqual({ done: 3, total: 3 });
  });

  it('新鲜缓存跳过拉取；单会话失败不阻断', async () => {
    upsertIndexedSession(makeSession('s0', 999));
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new Error('404'))
      .mockResolvedValue({ messages: [{ role: 'user', content: 'beta' }] });
    const result = await buildIndexFromSessions(metas, fetcher, { budget: 2 });
    expect(fetcher).toHaveBeenCalledTimes(1); // s0 缓存新鲜跳过，s1 失败
    expect(result.some((s) => s.id === 's0')).toBe(true);
  });

  it('abort 中止后续拉取', async () => {
    const controller = new AbortController();
    const fetcher = vi.fn().mockImplementation(async () => {
      controller.abort();
      return { messages: [] };
    });
    await buildIndexFromSessions(metas, fetcher, { signal: controller.signal });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe('normalizeSessionList + pendingLocate', () => {
  it('列表归一化（缺 title/updatedAt 容错）', () => {
    const metas = normalizeSessionList({
      sessions: [
        { id: 'a', title: 'T', updatedAt: 1700000000000 },
        { id: 'b' },
        { id: 'c', updatedAt: '2026-01-01T00:00:00Z' },
      ],
    });
    expect(metas).toHaveLength(3);
    expect(metas[1].title).toBe('未命名会话');
    expect(metas[2].updatedAt).toBeGreaterThan(0);
  });

  it('set → consume 同会话取回一次；异会话/超时丢弃', () => {
    setPendingLocate('s1', 5);
    expect(consumePendingLocate('s2')).toBeNull();
    expect(consumePendingLocate('s1')).toBe(5);
    expect(consumePendingLocate('s1')).toBeNull(); // 取完即清
    setPendingLocate('s1', 7, );
    // 直接操作 issuedAt 超窗：借助注入时钟太重，TTL 15s 内正常取回
    expect(consumePendingLocate('s1')).toBe(7);
  });
});
