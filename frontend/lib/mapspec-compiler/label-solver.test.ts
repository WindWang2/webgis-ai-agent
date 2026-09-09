/**
 * V6（ADR-0120 W6）：导出标签碰撞求解 —— TS 差分回归。
 *
 * 与 Python 参照实现（tests/cartography/test_label_collision_export.py）
 * 消费同一批 fixtures，逐坐标一致（3 位小数对齐）—— 差分 oracle。
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  estimateLabelBox,
  MAX_LABELS_PER_EXPORT,
  solveExportLabels,
  type CollisionLabel,
} from './label-solver';

interface Fixture {
  name: string;
  viewport: number[];
  labels: Array<CollisionLabel & Record<string, unknown>>;
  maxLabels?: number;
  expected: {
    placements: Array<{ id: string; x: number; y: number; angle: number; status: string; reason: string }>;
    stats: { total: number; placed: number; suppressed: number; collisions: number };
    budgetExceeded: boolean;
  };
}

const fixtureDir = join(__dirname, '../../../tests/cartography/golden_corpus/label_collision');
const fixtures = readdirSync(fixtureDir)
  .filter((f) => f.endsWith('.json'))
  .sort()
  .map((f) => JSON.parse(readFileSync(join(fixtureDir, f), 'utf-8')) as Fixture);

const round3 = (n: number) => Math.round(n * 1000) / 1000;

describe('label solver 差分 parity（W6）', () => {
  it('fixtures 数量不缩水', () => {
    expect(fixtures.length).toBeGreaterThanOrEqual(6);
  });

  it.each(fixtures.map((f) => [f.name, f] as const))('%s 与 golden 逐坐标一致', (_name, fx) => {
    const sol = solveExportLabels(
      fx.labels.map((l) => ({ ...l, fontSize: l.font_size as number })) as CollisionLabel[],
      fx.viewport,
      fx.maxLabels ?? MAX_LABELS_PER_EXPORT,
    );
    expect(sol.placements.map((p) => ({
      id: p.id, x: round3(p.x), y: round3(p.y), angle: round3(p.angle),
      status: p.status, reason: p.reason,
    }))).toEqual(fx.expected.placements);
    expect(sol.stats).toEqual(fx.expected.stats);
    expect(sol.budgetExceeded).toBe(fx.expected.budgetExceeded);
  });

  it('确定性重复求解', () => {
    const labels: CollisionLabel[] = Array.from({ length: 8 }, (_, i) => ({
      id: `L${i}`, text: `点${i}`, kind: 'point' as const, x: 50 + i, y: 50,
      angle: 0, fontSize: 11, priority: i,
    }));
    const s1 = solveExportLabels(labels, [0, 0, 200, 200]);
    const s2 = solveExportLabels(labels, [0, 0, 200, 200]);
    expect(s1).toEqual(s2);
  });

  it('CJK 宽度口径与 Python 一致（ estimateLabelBox ）', () => {
    // CJK 1.0em：两个汉字 fs=12 → w=24, h=14.4（浮点 3 位内近似）
    const [w1, h1] = estimateLabelBox('城市', 12);
    expect(w1).toBe(24);
    expect(h1).toBeCloseTo(14.4, 3);
    // 窄字符 0.6em：AB fs=10 → w=12
    expect(estimateLabelBox('AB', 10)).toEqual([12, 12]);
    expect(estimateLabelBox('', 10)).toEqual([0, 12]);
  });
});
