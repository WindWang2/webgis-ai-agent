/**
 * V6（ADR-0120 W6）：导出标签碰撞求解 —— TS 差分回归。
 *
 * 与 Python 参照实现（tests/cartography/test_label_collision_export.py）
 * 消费同一批 fixtures，逐坐标一致（3 位小数对齐）—— 差分 oracle。
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { Box } from '@/lib/label-geometry';
import {
  estimateLabelBox,
  MAX_LABELS_PER_EXPORT,
  MAX_OBSTACLES_PER_SOLVE,
  solveExportLabels,
  type CollisionLabel,
} from './label-solver';

interface Fixture {
  name: string;
  viewport: number[];
  labels: Array<CollisionLabel & Record<string, unknown>>;
  maxLabels?: number;
  /** W2.1（ADR-0283 D3）：图元障碍 AABB（[x1,y1,x2,y2]；缺省 = 无障碍）。 */
  obstacles?: Box[];
  expected: {
    placements: Array<{ id: string; x: number; y: number; angle: number; status: string; reason: string }>;
    stats: {
      total: number; placed: number; suppressed: number; collisions: number;
      obstacle_hits?: number; obstacles_truncated?: number;
    };
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
      fx.obstacles,
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

// ── W2.1（ADR-0283 D3）：标注×图元障碍（与 Python test_label_obstacles_v1 同构）──
describe('label solver obstacles（W2.1）', () => {
  /** 与 Python _label 同构："A" fs=10 → 盒 6×12、偏移 7.5。 */
  const label = (id = 'A'): CollisionLabel => ({
    id, text: 'A', kind: 'point', x: 100, y: 100, angle: 0, fontSize: 10, priority: 0,
  });
  const vp = [0, 0, 200, 200];

  it('障碍盖住全部 8 方位候选 → suppressed reason=obstacle', () => {
    const sol = solveExportLabels([label()], vp, MAX_LABELS_PER_EXPORT, [[85, 85, 115, 125]]);
    expect(sol.placements[0].status).toBe('suppressed');
    expect(sol.placements[0].reason).toBe('obstacle');
    expect(sol.stats.obstacle_hits).toBe(1);
    expect(sol.stats.collisions).toBe(0);
  });

  it('有避开障碍的候选位 → 放置在该位', () => {
    const sol = solveExportLabels([label()], vp, MAX_LABELS_PER_EXPORT, [[106.5, 90, 130, 126]]);
    expect(sol.placements[0]).toMatchObject({ status: 'placed', x: 100, y: 92.5, reason: '' });
    expect(sol.stats.obstacle_hits).toBe(0);
  });

  it('贴边障碍（x1 == 候选 x2）不命中 → 首选位可放', () => {
    const sol = solveExportLabels([label()], vp, MAX_LABELS_PER_EXPORT, [[113.5, 85, 140, 130]]);
    expect(sol.placements[0]).toMatchObject({ status: 'placed', x: 107.5, y: 107.5 });
  });

  it('line 标签单候选被障碍覆盖 → suppressed reason=obstacle', () => {
    const sol = solveExportLabels(
      [{ ...label('L1'), kind: 'line' }],
      vp,
      MAX_LABELS_PER_EXPORT,
      [[80, 80, 120, 120]],
    );
    expect(sol.placements[0]).toMatchObject({ status: 'suppressed', reason: 'obstacle' });
    expect(sol.stats.obstacle_hits).toBe(1);
  });

  it('障碍为空列表/缺省 → 与既有形态逐字段一致（兼容锁定）', () => {
    const labels: CollisionLabel[] = [
      label('A'),
      { ...label('B'), x: 101, y: 100.5, priority: 1 },
    ];
    const base = solveExportLabels(labels, vp);
    const withEmpty = solveExportLabels(labels, vp, MAX_LABELS_PER_EXPORT, []);
    expect(withEmpty.placements).toEqual(base.placements);
    expect(withEmpty.stats).toEqual(base.stats);
    expect(base.stats).not.toHaveProperty('obstacle_hits');
  });

  it(`障碍数上限 ${MAX_OBSTACLES_PER_SOLVE}，超出截断 + obstacles_truncated 计数`, () => {
    expect(MAX_OBSTACLES_PER_SOLVE).toBe(200);
    const filler: Box[] = Array.from({ length: MAX_OBSTACLES_PER_SOLVE }, () => [0, 0, 1, 1]);
    const sol = solveExportLabels(
      [label()], vp, MAX_LABELS_PER_EXPORT, [...filler, [85, 85, 115, 125]],
    );
    expect(sol.stats.obstacles_truncated).toBe(1);
    expect(sol.placements[0]).toMatchObject({ status: 'placed', x: 107.5, y: 107.5 });
  });
});
