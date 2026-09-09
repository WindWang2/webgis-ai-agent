/**
 * Export Label Collision Solver — portable twin subset (V6, ADR-0120 W6).
 *
 * app/lib/cartography/label_collision.py 的 1:1 TS 移植：导出孪生在
 * `layout.labels.collision === "deterministic"` 时共用的确定性碰撞求解。
 * 跨孪生差分由共享 fixtures 锁定（tests/cartography/golden_corpus/
 * label_collision/，坐标 3 位小数对齐）。纯函数、无随机、无 locale。
 */

export const MAX_LABELS_PER_EXPORT = 400;

/** 点标注 8 方位候选序（Python DECLUTTER_OFFSETS 同表；右上最优）。 */
const DECLUTTER_OFFSETS: ReadonlyArray<readonly [number, number]> = [
  [1, 1], [1, 0], [1, -1], [0, -1],
  [-1, -1], [-1, 0], [-1, 1], [0, 1],
];

const CJK_RANGES: ReadonlyArray<readonly [number, number]> = [
  [0x3000, 0x303f], [0x3400, 0x4dbf], [0x4e00, 0x9fff],
  [0xf900, 0xfaff], [0xff00, 0xffef],
];

function isCjk(ch: string): boolean {
  const o = ch.codePointAt(0) ?? 0;
  return CJK_RANGES.some(([lo, hi]) => o >= lo && o <= hi);
}

/** CJK 1.0em / 其余 0.6em 加权宽；高 = 1.2em（Python 同口径）。 */
export function estimateLabelBox(text: string, fontSize: number): [number, number] {
  if (!text) return [0, fontSize * 1.2];
  let em = 0;
  for (const ch of text) em += isCjk(ch) ? 1.0 : 0.6;
  return [em * fontSize, fontSize * 1.2];
}

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

type Box = [number, number, number, number];

function overlaps(a: Box, b: Box): boolean {
  return a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
}

function insideViewport(box: Box, vp: readonly number[]): boolean {
  return vp[0] <= box[0] && box[2] <= vp[2] && vp[1] <= box[1] && box[3] <= vp[3];
}

function keepUpright(deg: number): number {
  let a = deg % 360;
  if (a > 180) a -= 360;
  else if (a <= -180) a += 360;
  if (a > 90 || a < -90) a = (a + 180) % 360;
  return a;
}

function cornerBox(x: number, y: number, w: number, h: number): Box {
  return [x, y, x + w, y + h];
}

function centeredBox(x: number, y: number, w: number, h: number, angleDeg: number): Box {
  const a = (angleDeg * Math.PI) / 180;
  const c = Math.cos(a);
  const s = Math.sin(a);
  const hw = w / 2;
  const hh = h / 2;
  const xs: number[] = [];
  const ys: number[] = [];
  for (const sx of [-hw, hw]) {
    for (const sy of [-hh, hh]) {
      xs.push(x + sx * c - sy * s);
      ys.push(y + sx * s + sy * c);
    }
  }
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

/** 均匀格网 AABB 索引（Python _Grid 同构）。 */
class Grid {
  private cell: number;
  private cells = new Map<string, Box[]>();

  constructor(cell: number) {
    this.cell = cell > 0 ? cell : 1e-6;
  }

  private key(cx: number, cy: number): string {
    return `${cx}:${cy}`;
  }

  private span(box: Box): [number, number, number, number] {
    return [
      Math.floor(box[0] / this.cell), Math.floor(box[2] / this.cell),
      Math.floor(box[1] / this.cell), Math.floor(box[3] / this.cell),
    ];
  }

  insert(box: Box): void {
    const [x0, x1, y0, y1] = this.span(box);
    for (let cx = x0; cx <= x1; cx++) {
      for (let cy = y0; cy <= y1; cy++) {
        const k = this.key(cx, cy);
        const arr = this.cells.get(k);
        if (arr) arr.push(box);
        else this.cells.set(k, [box]);
      }
    }
  }

  collides(box: Box): boolean {
    const [x0, x1, y0, y1] = this.span(box);
    for (let cx = x0; cx <= x1; cx++) {
      for (let cy = y0; cy <= y1; cy++) {
        const arr = this.cells.get(this.key(cx, cy));
        if (arr) for (const other of arr) if (overlaps(box, other)) return true;
      }
    }
    return false;
  }
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
    if (!f.text.trim()) {
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
  const grid = new Grid(cell);

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
