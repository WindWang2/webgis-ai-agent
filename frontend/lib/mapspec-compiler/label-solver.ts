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
  stats: { total: number; placed: number; suppressed: number; collisions: number };
  budgetExceeded: boolean;
}

/**
 * 确定性导出标签求解（单遍贪心；同输入恒同输出）。
 * 预算：features 超过 maxLabels 时排序尾部溢出抑制（reason="budget"）。
 */
export function solveExportLabels(
  features: CollisionLabel[],
  viewport: readonly number[],
  maxLabels: number = MAX_LABELS_PER_EXPORT,
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
  for (const { f, w, h } of prepared) {
    let chosen: { cx: number; cy: number; angle: number; box: Box } | null = null;
    if (f.kind === 'point') {
      const offset = f.fontSize * 0.75;
      for (const [dx, dy] of DECLUTTER_OFFSETS) {
        const cx = f.x + dx * offset;
        const cy = f.y + dy * offset;
        const box = cornerBox(cx, cy, w, h);
        if (insideViewport(box, viewport) && !grid.collides(box)) {
          chosen = { cx, cy, angle: 0, box };
          break;
        }
      }
      if (chosen === null) {
        collisions += 1;
        placements.push({ id: f.id, x: f.x, y: f.y, angle: 0, status: 'suppressed', reason: 'collision' });
        continue;
      }
    } else {
      const ang = keepUpright(f.angle);
      const box = centeredBox(f.x, f.y, w, h, ang);
      if (insideViewport(box, viewport) && !grid.collides(box)) {
        chosen = { cx: f.x, cy: f.y, angle: ang, box };
      } else {
        collisions += 1;
        placements.push({ id: f.id, x: f.x, y: f.y, angle: 0, status: 'suppressed', reason: 'collision' });
        continue;
      }
    }

    placed += 1;
    placements.push({ id: f.id, x: chosen.cx, y: chosen.cy, angle: chosen.angle, status: 'placed', reason: '' });
    grid.insert(chosen.box);
  }

  return {
    placements,
    stats: {
      total: features.length,
      placed,
      suppressed: features.length - placed,
      collisions,
    },
    budgetExceeded,
  };
}
