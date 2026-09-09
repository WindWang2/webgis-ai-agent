/**
 * V6（ADR-0120 W5）：legend 条目单源 —— 双语言 parity（前端侧）。
 *
 * 与后端 pytest 消费同一批 golden fixtures（expected 手写 oracle）。
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { deriveLegendModel, deriveLegendTitle } from '@/lib/map-kit/legend-model';
import type { LegendSpec } from '@/lib/map-kit/types';

interface ModelCase {
  legendSpec: LegendSpec | null;
  expected: {
    kind: string;
    title: string;
    entries: Array<{ label: string; color: string; kind: string }>;
    hasNodata: boolean;
  } | null;
}

const fixtureDir = join(__dirname, '../../../tests/cartography/golden_corpus/legend_model');

function loadCases(): Array<[string, ModelCase]> {
  const cases: Array<[string, ModelCase]> = [];
  for (const f of readdirSync(fixtureDir).filter((x) => x.endsWith('.json')).sort()) {
    const data = JSON.parse(readFileSync(join(fixtureDir, f), 'utf-8'));
    if (data.cases) {
      data.cases.forEach((c: ModelCase, i: number) => cases.push([`${data.name ?? f}#${i}`, c]));
    } else {
      cases.push([data.name, data as ModelCase]);
    }
  }
  return cases;
}

describe('legend model 跨语言 parity（W5）', () => {
  it.each(loadCases().map(([name, c]) => [name, c] as const))('%s 与 golden 一致', (_name, c) => {
    const model = deriveLegendModel(c.legendSpec);
    if (c.expected === null) {
      expect(model).toBeNull();
      return;
    }
    expect(model).not.toBeNull();
    expect(model!.kind).toBe(c.expected.kind);
    expect(model!.title).toBe(c.expected.title);
    expect(model!.hasNodata).toBe(c.expected.hasNodata);
    expect(model!.entries).toEqual(c.expected.entries);
  });

  it('标题兜底单源（模型 null 时 oracle 语义仍需标题）', () => {
    expect(deriveLegendTitle(null)).toBe('图例');
    expect(deriveLegendTitle({ field: 'zone' })).toBe('字段: zone');
    expect(deriveLegendTitle({ title: 'T', field: 'zone' })).toBe('T');
  });
});
