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

/** 自身图元豁免的障碍种类（G16 geometry_v1，与 Python
 *  OWN_ANCHOR_EXEMPT_KINDS 同集）：线/面边界标签沿自身墨迹设计放置；
 *  circle 恒不豁免（点符号避让是设计意图）。 */
const OWN_ANCHOR_EXEMPT_KINDS = new Set(['line', 'polygon_boundary']);

/** 自动项 rank 排序基（G16）：带 userPriority 的自动项恒先于纯自动项。 */
const AUTO_RANK_BASE = 1_000_000;

/** 带身份的图元障碍（G16 geometry_v1；Python ObstacleBox 孪生）。
 *  裸 Box 元组 = legacy circles_legacy 语义（无身份、无豁免）。 */
export interface ObstacleBox {
  box: Box;
  kind: 'circle' | 'line' | 'polygon_boundary' | 'zone' | 'component';
  layerId?: string;
  featureId?: string;
}


/** fit_label_text 移植：>maxChars 截为前 maxChars-1 + "…"（code point 口径）。 */
export function fitLabel(text: string, maxChars = 60, ellipsis = '…'): [string, boolean] {
  const s = String(text);
  const cap = maxChars < 1 ? 1 : maxChars;
  const points = Array.from(s);
  if (points.length <= cap) return [s, false];
  return [points.slice(0, cap - 1).join('') + ellipsis, true];
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
  /** G16 authoring（全 optional —— 缺省 = legacy 行为逐字节不变）。 */
  hidden?: boolean;
  pinned?: boolean;
  /** canonical em 偏移（相对自身字号；+dy 屏幕向下，同 MapLibre text-offset）。 */
  offsetEm?: [number, number];
  userPriority?: number;
  layerId?: string;
  featureId?: string;
}

export interface CollisionPlacement {
  id: string;
  x: number;
  y: number;
  angle: number;
  status: 'placed' | 'suppressed';
  reason: string;
  /** G16：pinned 放置（user-wins —— 恒放置，冲突如实报告）。 */
  pinned?: boolean;
  conflict?: boolean;
}

export interface CollisionSolution {
  placements: CollisionPlacement[];
  /** W2.1：obstacle_hits/obstacles_truncated 仅在传入非空障碍时携带
   *  （键名与 Python 孪生 stats 逐字对齐 —— fixture 对拍口径）。 */
  stats: {
    total: number; placed: number; suppressed: number; collisions: number;
    obstacle_hits?: number; obstacles_truncated?: number;
    /** G16 authoring stats（仅 authoring 输入在场时出现 —— 对拍兼容）。 */
    hidden_by_user?: number;
    pinned_placed?: number;
    pinned_conflicts?: number;
    pinned_obstacle_overlaps?: number;
    pinned_offscreen?: number;
  };
  budgetExceeded: boolean;
}

/**
 * 确定性导出标签求解（单遍贪心；同输入恒同输出）。
 * 预算：auto features 超过 maxLabels 时排序尾部溢出抑制（reason="budget"）。
 *
 * 图元障碍（W2.1）：obstacles 为 AABB（[x1,y1,x2,y2]）或 ObstacleBox
 * （G16 带身份）。候选盒与任一障碍严格重叠（贴边不算）→ 候选位不可用；
 * 全占 → 抑制（reason="obstacle"）。障碍超 MAX_OBSTACLES_PER_SOLVE
 * 保留前 N，截断数记 stats.obstacles_truncated。obstacles 缺省/空 →
 * 输出与无障碍形态完全一致（兼容锁定）。
 *
 * G16 authoring（Python solve_export_labels 1:1 孪生；无 authoring 输入
 * = legacy 行为逐字节不变 —— golden corpus 双侧锁定）：
 * - hidden → suppressed（reason="hidden_by_user"），不占预算；
 * - pinned → 先于全部自动项按 (userPriority ?? 0, id) 放置于
 *   锚点+offsetEm×fontSize 中心位（point 发左上角、line/polygon 发中心
 *   —— 与渲染映射分支同口径）。恒放置（user-wins）；冲突置 conflict
 *   并计 stats 分型（pinned_conflicts / pinned_obstacle_overlaps /
 *   pinned_offscreen）。empty_text 仍抑制；
 * - userPriority（自动项）→ 排序键 (userPriority, priority, id)，恒先于
 *   纯自动项（rank + AUTO_RANK_BASE）；
 * - 障碍身份豁免：ObstacleBox.kind ∈ OWN_ANCHOR_EXEMPT_KINDS 且
 *   (layerId, featureId) 与标签一致 → 不拦此标签；circle 恒不豁免；
 *   匿名 Box 无豁免（legacy 契约）。
 */
export function solveExportLabels(
  features: CollisionLabel[],
  viewport: readonly number[],
  maxLabels: number = MAX_LABELS_PER_EXPORT,
  obstacles?: readonly (Box | ObstacleBox)[],
): CollisionSolution {
  const hiddenFs = features.filter((f) => f.hidden);
  const pinnedFs = features
    .filter((f) => f.pinned && !f.hidden)
    .sort(
      (a, b) =>
        (a.userPriority ?? 0) - (b.userPriority ?? 0) ||
        (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
    );
  const autoOrdered = features
    .filter((f) => !f.hidden && !f.pinned)
    .sort(
      (a, b) => {
        const ka = a.userPriority ?? a.priority + AUTO_RANK_BASE;
        const kb = b.userPriority ?? b.priority + AUTO_RANK_BASE;
        return (
          ka - kb ||
          ((a.userPriority ?? 0) - (b.userPriority ?? 0)) ||
          (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)
        );
      },
    );
  const hasAuthoring =
    hiddenFs.length > 0 ||
    pinnedFs.length > 0 ||
    features.some((f) => f.userPriority !== undefined);

  const placements: CollisionPlacement[] = [];
  for (const f of hiddenFs) {
    placements.push({ id: f.id, x: f.x, y: f.y, angle: 0, status: 'suppressed', reason: 'hidden_by_user' });
  }

  const budgetExceeded = autoOrdered.length > maxLabels;
  const gated = autoOrdered.slice(0, maxLabels);
  for (const f of autoOrdered.slice(maxLabels)) {
    placements.push({ id: f.id, x: f.x, y: f.y, angle: 0, status: 'suppressed', reason: 'budget' });
  }

  const normalized: ObstacleBox[] = obstacles
    ? obstacles.map((ob) =>
        Array.isArray(ob)
          ? { box: ob as Box, kind: 'circle' as const }
          : (ob as ObstacleBox),
      )
    : [];
  const activeObstacles = normalized.slice(0, MAX_OBSTACLES_PER_SOLVE);
  const obstaclesTruncated = Math.max(0, normalized.length - MAX_OBSTACLES_PER_SOLVE);
  const obstacleHit = (label: CollisionLabel, box: Box): boolean =>
    activeObstacles.some((ob) => {
      if (
        ob.featureId !== undefined &&
        OWN_ANCHOR_EXEMPT_KINDS.has(ob.kind) &&
        label.layerId !== undefined &&
        label.featureId !== undefined &&
        ob.layerId === label.layerId &&
        ob.featureId === label.featureId
      ) {
        return false; // 自身线/面墨迹不拦自身标签（geometry_v1 语义）
      }
      return overlaps(box, ob.box);
    });

  const pinnedPrepared: Array<{ f: CollisionLabel; w: number; h: number }> = [];
  let pinnedConflicts = 0;
  let pinnedObstacleOverlaps = 0;
  let pinnedOffscreen = 0;
  for (const f of pinnedFs) {
    if (!f.text.trim() || f.fontSize <= 0) {
      placements.push({ id: f.id, x: f.x, y: f.y, angle: 0, status: 'suppressed', reason: 'empty_text' });
      continue;
    }
    const [w, h] = estimateLabelBox(f.text, f.fontSize);
    pinnedPrepared.push({ f, w, h });
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
  const allSized = pinnedPrepared.concat(prepared);
  if (allSized.length > 0) {
    let m = 0;
    for (const p of allSized) m = Math.max(m, p.w, p.h);
    cell = 2 * m;
  }
  const grid = new Grid(Math.max(cell, 1));

  // pinned 先放置（user-wins：恒放置，冲突如实报告）
  let placed = 0;
  for (const { f, w, h } of pinnedPrepared) {
    const [dxEm, dyEm] = f.offsetEm ?? [0, 0];
    const cx = f.x + dxEm * f.fontSize;
    const cy = f.y + dyEm * f.fontSize;
    let px: number;
    let py: number;
    let angle: number;
    let box: Box;
    if (f.kind === 'point') {
      // point 渲染映射消费左上角坐标（盒角 → 基线补偿）。
      px = cx - w / 2;
      py = cy - h / 2;
      angle = 0;
      box = [px, py, px + w, py + h];
    } else {
      angle = keepUpright(f.angle);
      box = centeredBox(cx, cy, w, h, angle);
      px = cx;
      py = cy;
    }
    let conflict = false;
    if (grid.collides(box)) {
      conflict = true;
      pinnedConflicts += 1;
    }
    if (activeObstacles.length > 0 && obstacleHit(f, box)) {
      conflict = true;
      pinnedObstacleOverlaps += 1;
    }
    if (!insideViewport(box, viewport)) {
      conflict = true;
      pinnedOffscreen += 1;
    }
    placed += 1;
    placements.push({ id: f.id, x: px, y: py, angle, status: 'placed', reason: '', pinned: true, conflict });
    grid.insert(box);
  }

  // 自动项（legacy 贪心；grid 已被 pinned 占位）
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
        if (activeObstacles.length > 0 && obstacleHit(f, box)) {
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
      if (activeObstacles.length > 0 && obstacleHit(f, box)) {
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
  if (hasAuthoring) {
    stats.hidden_by_user = hiddenFs.length;
    stats.pinned_placed = pinnedPrepared.length;
    if (pinnedConflicts > 0) stats.pinned_conflicts = pinnedConflicts;
    if (pinnedObstacleOverlaps > 0) stats.pinned_obstacle_overlaps = pinnedObstacleOverlaps;
    if (pinnedOffscreen > 0) stats.pinned_offscreen = pinnedOffscreen;
  }

  return {
    placements,
    stats,
    budgetExceeded,
  };
}
