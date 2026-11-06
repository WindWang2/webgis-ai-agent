/**
 * Export Label Collision Solver — portable twin subset (V6, ADR-0120 W6).
 *
 * app/lib/cartography/label_collision.py 的 1:1 TS 移植：导出孪生在
 * `layout.labels.collision === "deterministic"` 时共用的确定性碰撞求解。
 * 跨孪生差分由共享 fixtures 锁定（tests/cartography/golden_corpus/
 * label_collision/，坐标 3 位小数对齐）。纯函数、无随机、无 locale。
 */

import {
  DECLUTTER_OFFSETS,
  type Box,
  centeredBox,
  cornerBox,
  estimateLabelBox,
  insideViewport,
  keepUpright,
  overlaps,
  SpatialGrid as Grid,
} from '@/lib/label-geometry';

export { estimateLabelBox } from '@/lib/label-geometry';

export const MAX_LABELS_PER_EXPORT = 400;

/** 单次求解接受的图元障碍盒上限（超出截断 → stats.obstacles_truncated；
 *  Python MAX_OBSTACLES_PER_SOLVE 同值，W2.1/ADR-0283 D3 资源包络）。 */
export const MAX_OBSTACLES_PER_SOLVE = 200;


/** fit_label_text 移植：>maxChars 截为前 maxChars-1 + "…"（code point 口径）。 */
export function fitLabel(text: string, maxChars = 60, ellipsis = '…'): [string, boolean] {
  const s = String(text);
  const cap = maxChars < 1 ? 1 : maxChars;
  if (s.length <= cap) return [s, false];
  return [s.slice(0, cap - 1) + ellipsis, true];
}
export interface CollisionLabel {
  id: string;
  text: string;
  kind: 'point' | 'line' | 'polygon';
  x: number;
  y: number;
  /** line/polygon 沿线角（度，求解器内部 keep-upright）。 */
  angle: number;
  fontSize: number;
  priority: number;
}

export interface CollisionPlacement {
  id: string;
  x: number;
  y: number;
  angle: number;
  status: 'placed' | 'suppressed';
  reason: string;
}

export interface CollisionSolution {
  placements: CollisionPlacement[];
  /** W2.1：obstacle_hits/obstacles_truncated 仅在传入非空障碍时携带
   *  （键名与 Python 孪生 stats 逐字对齐 —— fixture 对拍口径）。 */
  stats: {
    total: number; placed: number; suppressed: number; collisions: number;
    obstacle_hits?: number; obstacles_truncated?: number;
  };
  budgetExceeded: boolean;
}

/**
 * 确定性导出标签求解（单遍贪心；同输入恒同输出）。
 * 预算：features 超过 maxLabels 时排序尾部溢出抑制（reason="budget"）。
 *
 * 图元障碍（W2.1，ADR-0283 D3）：obstacles 为图元 AABB（[x1,y1,x2,y2]），
 * 候选盒与任一障碍严格重叠（贴边不算）→ 候选位不可用；全占 → 抑制
 * （reason="obstacle"）。障碍超 MAX_OBSTACLES_PER_SOLVE 保留前 N，
 * 截断数记 stats.obstacles_truncated。obstacles 缺省/空 → 输出与无障碍
 * 形态完全一致（兼容锁定）。障碍盒不带身份；跨层同源的点符号也参与
 * 避让，全候选被图元遮挡时按同一规则抑制并披露。
 */
export function solveExportLabels(
  features: CollisionLabel[],
  viewport: readonly number[],
  maxLabels: number = MAX_LABELS_PER_EXPORT,
  obstacles?: readonly Box[],
): CollisionSolution {
  const ordered = [...features].sort(
    (a, b) => a.priority - b.priority || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
  const budgetExceeded = ordered.length > maxLabels;
  const gated = ordered.slice(0, maxLabels);

  const placements: CollisionPlacement[] = [];
  for (const f of ordered.slice(maxLabels)) {
    placements.push({ id: f.id, x: f.x, y: f.y, angle: 0, status: 'suppressed', reason: 'budget' });
  }

  const activeObstacles: Box[] = obstacles ? obstacles.slice(0, MAX_OBSTACLES_PER_SOLVE) : [];
  const obstaclesTruncated = obstacles
    ? Math.max(0, obstacles.length - MAX_OBSTACLES_PER_SOLVE)
    : 0;
  const obstacleHit = (box: Box): boolean =>
    activeObstacles.some((ob) => overlaps(box, ob));

  const prepared: Array<{ f: CollisionLabel; w: number; h: number }> = [];
  for (const f of gated) {
    // R2-M11：font_size<=0 → 格网失效（与 Python 孪生同口径抑制）
    if (!f.text.trim() || f.fontSize <= 0) {
      placements.push({ id: f.id, x: f.x, y: f.y, angle: 0, status: 'suppressed', reason: 'empty_text' });
      continue;
    }
    const [w, h] = estimateLabelBox(f.text, f.fontSize);
    prepared.push({ f, w, h });
  }

  let cell = 48;
  if (prepared.length > 0) {
    let m = 0;
    for (const p of prepared) m = Math.max(m, p.w, p.h);
    cell = 2 * m;
  }
  const grid = new Grid(Math.max(cell, 1));

  let placed = 0;
  let collisions = 0;
  let obstacleHits = 0;
  for (const { f, w, h } of prepared) {
    let chosen: { cx: number; cy: number; angle: number; box: Box } | null = null;
    if (f.kind === 'point') {
      const offset = f.fontSize * 0.75;
      let obstacleBlocked = false;
      for (const [dx, dy] of DECLUTTER_OFFSETS) {
        const cx = f.x + dx * offset;
        const cy = f.y + dy * offset;
        const box = cornerBox(cx, cy, w, h);
        if (!insideViewport(box, viewport) || grid.collides(box)) continue;
        if (activeObstacles.length > 0 && obstacleHit(box)) {
          // 该候选位仅因障碍不可用 —— 记证据，继续尝试后续候选位。
          obstacleBlocked = true;
          continue;
        }
        chosen = { cx, cy, angle: 0, box };
        break;
      }
      if (chosen === null) {
        if (obstacleBlocked) {
          obstacleHits += 1;
          placements.push({ id: f.id, x: f.x, y: f.y, angle: 0, status: 'suppressed', reason: 'obstacle' });
        } else {
          collisions += 1;
          placements.push({ id: f.id, x: f.x, y: f.y, angle: 0, status: 'suppressed', reason: 'collision' });
        }
        continue;
      }
    } else {
      const ang = keepUpright(f.angle);
      const box = centeredBox(f.x, f.y, w, h, ang);
      if (!(insideViewport(box, viewport) && !grid.collides(box))) {
        collisions += 1;
        placements.push({ id: f.id, x: f.x, y: f.y, angle: 0, status: 'suppressed', reason: 'collision' });
        continue;
      }
      if (activeObstacles.length > 0 && obstacleHit(box)) {
        obstacleHits += 1;
        placements.push({ id: f.id, x: f.x, y: f.y, angle: 0, status: 'suppressed', reason: 'obstacle' });
        continue;
      }
      chosen = { cx: f.x, cy: f.y, angle: ang, box };
    }

    placed += 1;
    placements.push({ id: f.id, x: chosen.cx, y: chosen.cy, angle: chosen.angle, status: 'placed', reason: '' });
    grid.insert(chosen.box);
  }

  const stats: CollisionSolution['stats'] = {
    total: features.length,
    placed,
    suppressed: features.length - placed,
    collisions,
  };
  if (activeObstacles.length > 0) {
    stats.obstacle_hits = obstacleHits;
    stats.obstacles_truncated = obstaclesTruncated;
  }

  return {
    placements,
    stats,
    budgetExceeded,
  };
}
