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
  type ObstacleBox,
} from './label-solver';

interface Fixture {
  name: string;
  viewport: number[];
  labels: Array<Record<string, unknown>>;
  maxLabels?: number;
  /** W2.1：裸 AABB = legacy 匿名；G16 geometry_v1 为带身份对象。 */
  obstacles?: Array<Box | { box: Box; kind: string; layer_id?: string; feature_id?: string }>;
  expected: {
    placements: Array<{
      id: string; x: number; y: number; angle: number; status: string; reason: string;
      pinned?: boolean; conflict?: boolean;
    }>;
    stats: {
      total: number; placed: number; suppressed: number; collisions: number;
      obstacle_hits?: number; obstacles_truncated?: number;
      hidden_by_user?: number; pinned_placed?: number;
      pinned_conflicts?: number; pinned_obstacle_overlaps?: number; pinned_offscreen?: number;
    };
    budgetExceeded: boolean;
  };
}

/** fixture（snake_case JSON）→ CollisionLabel（与 Python 加载器同映射）。 */
function labelOf(l: Record<string, unknown>): CollisionLabel {
  return {
    id: l.id as string,
    text: l.text as string,
    kind: l.kind as CollisionLabel['kind'],
    x: l.x as number,
    y: l.y as number,
    angle: (l.angle as number) ?? 0,
    fontSize: l.font_size as number,
    priority: (l.priority as number) ?? 0,
    ...(l.hidden !== undefined ? { hidden: l.hidden as boolean } : {}),
    ...(l.pinned !== undefined ? { pinned: l.pinned as boolean } : {}),
    ...(l.offset_em !== undefined ? { offsetEm: l.offset_em as [number, number] } : {}),
    ...(l.user_priority !== undefined ? { userPriority: l.user_priority as number } : {}),
    ...(l.layer_id !== undefined ? { layerId: l.layer_id as string } : {}),
    ...(l.feature_id !== undefined ? { featureId: l.feature_id as string } : {}),
  };
}

function obstacleOf(ob: NonNullable<Fixture['obstacles']>[number]): Box | ObstacleBox {
  if (Array.isArray(ob)) return ob as Box;
  const o = ob as { box: Box; kind: string; layer_id?: string; feature_id?: string };
  return { box: o.box, kind: o.kind as ObstacleBox['kind'], layerId: o.layer_id, featureId: o.feature_id };
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
      fx.labels.map(labelOf),
      fx.viewport,
      fx.maxLabels ?? MAX_LABELS_PER_EXPORT,
      fx.obstacles?.map(obstacleOf),
    );
    expect(sol.placements.map((p) => {
      const row: Record<string, unknown> = {
        id: p.id, x: round3(p.x), y: round3(p.y), angle: round3(p.angle),
        status: p.status, reason: p.reason,
      };
      if (p.pinned) row.pinned = true;
      if (p.conflict) row.conflict = true;
      return row;
    })).toEqual(fx.expected.placements);
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

// ── G16：authoring 感知求解（与 Python test_label_authoring_solver 同构）──
describe('label solver authoring（G16）', () => {
  const vp = [0, 0, 200, 200];
  const p = (id: string, x: number, y: number, extra: Partial<CollisionLabel> = {}): CollisionLabel => ({
    id, text: id, kind: 'point', x, y, angle: 0, fontSize: 12, priority: 0, ...extra,
  });

  it('pinned 先放置；自动项绕行（用户决定不被移动）', () => {
    const sol = solveExportLabels(
      [p('A', 100, 100, { priority: 0 }), p('P', 100, 100, { priority: 9, pinned: true })],
      vp,
    );
    const by = Object.fromEntries(sol.placements.map((q) => [q.id, q]));
    expect(by.P).toMatchObject({ status: 'placed', pinned: true, conflict: false });
    expect(by.P.pinned).toBe(true);
    if (by.A.status === 'placed') {
      expect([by.A.x, by.A.y]).not.toEqual([100, 100]);
    }
  });

  it('hidden → suppressed(hidden_by_user)，不占预算', () => {
    const labels = [
      ...Array.from({ length: 5 }, (_, i) => p(`H${i}`, 10 + i, 10, { hidden: true })),
      ...Array.from({ length: 4 }, (_, i) => p(`A${i}${i}`, 10 + i * 9, 100, { priority: i })),
    ];
    const sol = solveExportLabels(labels, vp, 3);
    expect(sol.placements.filter((q) => q.reason === 'hidden_by_user')).toHaveLength(5);
    expect(sol.placements.filter((q) => q.reason === 'budget')).toHaveLength(1);
    expect(sol.stats.hidden_by_user).toBe(5);
    expect(sol.budgetExceeded).toBe(true);
  });

  it('pinned 越界 → 保持意图 + conflict + stats.pinned_offscreen', () => {
    const sol = solveExportLabels([p('P', 5, 5, { pinned: true })], vp);
    expect(sol.placements[0]).toMatchObject({ status: 'placed', pinned: true, conflict: true });
    expect(sol.stats.pinned_offscreen).toBe(1);
  });

  it('自身线墨迹豁免；异要素/异层/匿名不豁免', () => {
    const own: ObstacleBox = { box: [90, 94, 110, 106], kind: 'line', layerId: 'roads', featureId: 'r1' };
    const mk = (layerId: string, featureId: string): CollisionLabel => ({
      id: 'X', text: '路名', kind: 'line', x: 100, y: 100, angle: 0,
      fontSize: 12, priority: 0, layerId, featureId,
    });
    expect(solveExportLabels([mk('roads', 'r1')], vp, 400, [own]).placements[0].status).toBe('placed');
    expect(solveExportLabels([mk('roads', 'r9')], vp, 400, [own]).placements[0].reason).toBe('obstacle');
    expect(solveExportLabels([mk('L2', 'r1')], vp, 400, [own]).placements[0].reason).toBe('obstacle');
    expect(solveExportLabels([mk('roads', 'r1')], vp, 400, [[90, 94, 110, 106]]).placements[0].reason).toBe('obstacle');
    const circle: ObstacleBox = { box: [88, 88, 112, 112], kind: 'circle', layerId: 'pts', featureId: 'p1' };
    expect(
      solveExportLabels([p('A', 100, 100, { layerId: 'pts', featureId: 'p1' })], vp, 400, [circle])
        .placements[0].reason,
    ).toBe('obstacle');
  });

  it('offsetEm 按 em×字号换算（point 发左上角）', () => {
    const sol = solveExportLabels(
      [p('P', 100, 100, { pinned: true, offsetEm: [2, -1] })], vp);
    const q = sol.placements[0];
    const [w, h] = estimateLabelBox('P', 12);
    expect(q.x).toBeCloseTo(124 - w / 2, 9);
    expect(q.y).toBeCloseTo(88 - h / 2, 9);
  });

  it('legacy 输入无 authoring stats 键（兼容锁定）', () => {
    const sol = solveExportLabels(
      Array.from({ length: 6 }, (_, i) => p(`A${i}`, 30 + i * 17, 40 + i * 7, { priority: i })), vp);
    expect(sol.stats).not.toHaveProperty('hidden_by_user');
    expect(sol.stats).not.toHaveProperty('pinned_placed');
  });

  it('确定性：同输入（含 authoring）两次求解逐位相等', () => {
    const labels = [
      p('P1', 100, 100, { pinned: true, offsetEm: [1.5, 0.5], layerId: 'L', featureId: 'f1' }),
      p('H1', 40, 40, { hidden: true }),
      p('A1', 60, 60, { priority: 2 }),
      p('U1', 80, 80, { userPriority: -5 }),
    ];
    expect(solveExportLabels(labels, vp)).toEqual(solveExportLabels(labels, vp));
  });
});
