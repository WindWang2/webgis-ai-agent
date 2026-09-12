'use client';

/**
 * 命令面板模糊匹配：预排序 + 简单评分。
 *
 * 性能契约（§2 P1）：5000 条命令单次查询 <16ms。实现为 O(n·m) 子序列
 * 扫描 + 逐条 O(1) 附加分，配 `buildSearchIndex` 预 lowers 所有参与字段，
 * 避免查询期重复字符串分配。
 */

export interface FuzzyMatch {
  /** 总分，越高越靠前。无命中返回 null。 */
  score: number;
  /** 命中字符在 haystack 中的下标（用于面板高亮）。 */
  indices: number[];
}

const SCORE_SEQUENTIAL = 8;
const SCORE_WORD_START = 10;
const SCORE_HEAD = 12;
const SCORE_GAP = -1;

/** 单串模糊子序列匹配。query/haystack 需同为小写。 */
export function fuzzyMatch(query: string, haystack: string): FuzzyMatch | null {
  if (!query) return { score: 0, indices: [] };
  const h = haystack;
  const qlen = query.length;
  const hlen = h.length;
  if (qlen > hlen) return null;

  const indices: number[] = [];
  let score = 0;
  let qi = 0;
  let prevHit = -2;
  for (let hi = 0; hi < hlen && qi < qlen; hi++) {
    if (h[hi] !== query[qi]) continue;
    if (prevHit === hi - 1) score += SCORE_SEQUENTIAL;
    else if (hi === 0 || /[\s\-_./:·]/.test(h[hi - 1])) score += SCORE_WORD_START;
    if (hi === 0) score += SCORE_HEAD;
    if (prevHit < hi - 1 && prevHit >= 0) score += SCORE_GAP;
    score -= hi * 0.05; // 早命中优于晚命中（微扰，不破坏整数分级）
    indices.push(hi);
    prevHit = hi;
    qi++;
  }
  if (qi < qlen) return null;
  return { score, indices };
}

export interface IndexedCommand {
  id: string;
  title: string;
  group: string;
  keywords: string;
  /** title.lower() 预计算。 */
  lowerTitle: string;
  /** group.lower() 预计算。 */
  lowerGroup: string;
}

/** 查询前一次性建索引（预 lowers）。 */
export function buildSearchIndex(items: { id: string; title: string; group: string; keywords?: string }[]): IndexedCommand[] {
  return items.map((it) => ({
    id: it.id,
    title: it.title,
    group: it.group,
    keywords: it.keywords ?? '',
    lowerTitle: it.title.toLowerCase(),
    lowerGroup: it.group.toLowerCase(),
  }));
}

export interface ScoredHit {
  id: string;
  score: number;
  /** title 命中的字符下标（仅 title 匹配时返回；keyword 命中不高亮）。 */
  indices?: number[];
}

/**
 * 对索引做一次模糊过滤评分，按分降序返回。
 * title 权重 > keywords > group；同分保持索引序（稳定排序）。
 */
export function searchIndexed(index: IndexedCommand[], query: string): ScoredHit[] {
  const q = query.toLowerCase().trim();
  if (!q) return index.map((it) => ({ id: it.id, score: 0 }));
  const hits: ScoredHit[] = [];
  for (let i = 0; i < index.length; i++) {
    const it = index[i];
    const onTitle = fuzzyMatch(q, it.lowerTitle);
    if (onTitle) {
      hits.push({ id: it.id, score: onTitle.score + 40, indices: onTitle.indices });
      continue;
    }
    const onKeywords = it.keywords ? fuzzyMatch(q, it.keywords.toLowerCase()) : null;
    if (onKeywords) {
      hits.push({ id: it.id, score: onKeywords.score + 20 });
      continue;
    }
    const onGroup = fuzzyMatch(q, it.lowerGroup);
    if (onGroup) {
      hits.push({ id: it.id, score: onGroup.score });
    }
  }
  hits.sort((a, b) => b.score - a.score);
  return hits;
}
