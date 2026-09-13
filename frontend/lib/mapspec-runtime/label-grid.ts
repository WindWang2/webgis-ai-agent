/**
 * label-grid —— 交互侧确定性网格碰撞（V11 W4.2，ADR-0164）。
 *
 * 交互标注现状（ADR-0126）：MapLibre 内置 `text-allow-overlap:false` 逐帧
 * 贪心，无全局确定性避让。本模块给它第四件能力 —— **网格碰撞求解**
 * （与导出孪生 `mapspec-compiler/label-solver.ts` 的 Grid 同语义：estimate
 * box / 8 方位退让 / AABB 格网索引），产出每要素的 `text-offset` 与
 * `visibility`，MapLibre 内置避让保留为兜底（offset 后仍重叠由其吸收）。
 *
 * 确定性：无随机、无时钟；同输入两次求解逐位相等。
 * 后端对齐：盒估算与 `label_typography.estimate_label_box` 逐常量一致
 * （CJK 1.0em / 其他 0.6em，高 1.2em）。
 */

/** 盒估算（em 单位；与后端 estimate_label_box 同口径）。 */
export function estimateLabelBoxEm(text: string): { w: number; h: number } {
  if (!text) return { w: 0, h: 1.2 };
  let em = 0;
  for (const ch of text) em += isCJK(ch) ? 1.0 : 0.6;
  return { w: em, h: 1.2 };
}

const CJK_RANGES: Array<[number, number]> = [
  [0x3000, 0x303f], [0x3400, 0x4dbf], [0x4e00, 0x9fff],
  [0xf900, 0xfaff], [0xff00, 0xffef],
];

function isCJK(ch: string): boolean {
  const o = ch.codePointAt(0);
  if (o === undefined) return false;
  return CJK_RANGES.some(([lo, hi]) => lo <= o && o <= hi);
}

/** 8 方位候选序（GIS 惯例右上最优；与后端 DECLUTTER_CANDIDATE_OFFSETS 同表）。 */
const DECLUTTER_OFFSETS: Array<[number, number]> = [
  [1, 1], [1, 0], [1, -1], [0, -1], [-1, -1], [-1, 0], [-1, 1], [0, 1],
];

export interface GridLabelInput {
  id: string;
  text: string;
  /** 画布像素坐标（point 锚点 / line、polygon 中心）。 */
  x: number;
  y: number;
  kind: 'point' | 'line' | 'polygon';
  /** 小值优先（与后端 priority 语义一致）。 */
  priority: number;
  /** 字号（px；盒估算的 em 基准）。 */
  fontSize: number;
}

export interface GridPlacement {
  id: string;
  /** text-offset（px；MapLibre text-offset 语义，单位由消费方换算）。 */
  dx: number;
  dy: number;
  status: 'placed' | 'suppressed';
  reason: '' | 'collision' | 'empty_text' | 'out_of_viewport';
}

export interface GridCollisionOptions {
  /** 视口 [minX, minY, maxX, maxY]（px；标签须完整落在其中）。 */
  viewport: [number, number, number, number];
  /** 格宽（px；0 = 自动 = 最大标签边 ×2）。 */
  cellPx?: number;
  /** 点标注退让半径（px；缺省 = fontSize × 0.75，与后端同口径）。 */
  offsetPx?: number;
}

interface Box { x1: number; y1: number; x2: number; y2: number }

/** AABB 格网索引（与后端 LabelGrid 同构；插入序确定性）。 */
class SpatialGrid {
  private cells = new Map<string, Box[]>();

  constructor(private cell: number) {
    this.cell = cell > 0 ? cell : 1e-6;
  }

  private span(b: Box): [number, number, number, number] {
    return [
      Math.floor(b.x1 / this.cell), Math.floor(b.x2 / this.cell),
      Math.floor(b.y1 / this.cell), Math.floor(b.y2 / this.cell),
    ];
  }

  insert(b: Box): void {
    const [cx0, cx1, cy0, cy1] = this.span(b);
    for (let cx = cx0; cx <= cx1; cx += 1) {
      for (let cy = cy0; cy <= cy1; cy += 1) {
        const key = `${cx},${cy}`;
        const list = this.cells.get(key);
        if (list) list.push(b);
        else this.cells.set(key, [b]);
      }
    }
  }

  collides(b: Box): boolean {
    const [cx0, cx1, cy0, cy1] = this.span(b);
    for (let cx = cx0; cx <= cx1; cx += 1) {
      for (let cy = cy0; cy <= cy1; cy += 1) {
        for (const other of this.cells.get(`${cx},${cy}`) ?? []) {
          if (overlaps(b, other)) return true;
        }
      }
    }
    return false;
  }
}

function overlaps(a: Box, b: Box): boolean {
  // 严格重叠（贴边不算碰撞；与后端 _overlaps 同式）
  return a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2;
}

function insideViewport(b: Box, vp: [number, number, number, number]): boolean {
  return vp[0] <= b.x1 && b.x2 <= vp[2] && vp[1] <= b.y1 && b.y2 <= vp[3];
}

function boxFrom(
  cx: number, cy: number, w: number, h: number, angleDeg: number, kind: string,
): Box {
  if (kind === 'point') {
    return { x1: cx, y1: cy, x2: cx + w, y2: cy + h };
  }
  const a = (angleDeg * Math.PI) / 180;
  const c = Math.cos(a);
  const s = Math.sin(a);
  const hw = w / 2;
  const hh = h / 2;
  const xs: number[] = [];
  const ys: number[] = [];
  for (const sx of [-hw, hw]) {
    for (const sy of [-hh, hh]) {
      xs.push(cx + sx * c - sy * s);
      ys.push(cy + sx * s + sy * c);
    }
  }
  return { x1: Math.min(...xs), y1: Math.min(...ys), x2: Math.max(...xs), y2: Math.max(...ys) };
}

/**
 * 确定性网格碰撞求解（单遍贪心；priority 升序 + id 字典序稳定全序）。
 * MapLibre 内置避让保留为兜底：本求解先行抽稀/退让/抑制，残余重叠由
 * `text-allow-overlap:false` 吸收 —— 两层叠加，不是替换。
 */
export function solveGridCollision(
  inputs: GridLabelInput[],
  opts: GridCollisionOptions,
): GridPlacement[] {
  const vp = opts.viewport;
  const ordered = [...inputs].sort(
    (a, b) => (a.priority - b.priority) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );

  const prepared: Array<{ f: GridLabelInput; w: number; h: number }> = [];
  const placements: GridPlacement[] = [];
  for (const f of ordered) {
    if (!f.text.trim() || f.fontSize <= 0) {
      placements.push({ id: f.id, dx: 0, dy: 0, status: 'suppressed', reason: 'empty_text' });
      continue;
    }
    const { w, h } = estimateLabelBoxEm(f.text);
    prepared.push({ f, w: w * f.fontSize, h: h * f.fontSize });
  }

  const maxEdge = prepared.reduce((m, p) => Math.max(m, p.w, p.h), 0);
  const cell = opts.cellPx && opts.cellPx > 0
    ? opts.cellPx
    : Math.max(2 * maxEdge, 1);
  const grid = new SpatialGrid(cell);
  const offset = opts.offsetPx && opts.offsetPx > 0
    ? opts.offsetPx
    : (prepared[0]?.f.fontSize ?? 12) * 0.75;

  for (const { f, w, h } of prepared) {
    let chosen: { dx: number; dy: number; box: Box } | null = null;
    if (f.kind === 'point') {
      for (const [ox, oy] of DECLUTTER_OFFSETS) {
        const dx = ox * offset;
        const dy = oy * offset;
        const box = boxFrom(f.x + dx, f.y + dy, w, h, 0, 'point');
        if (insideViewport(box, vp) && !grid.collides(box)) {
          chosen = { dx, dy, box };
          break;
        }
      }
    } else {
      const box = boxFrom(f.x, f.y, w, h, 0, f.kind);
      if (insideViewport(box, vp) && !grid.collides(box)) {
        chosen = { dx: 0, dy: 0, box };
      }
    }
    if (chosen) {
      grid.insert(chosen.box);
      placements.push({ id: f.id, dx: chosen.dx, dy: chosen.dy, status: 'placed', reason: '' });
    } else {
      placements.push({ id: f.id, dx: 0, dy: 0, status: 'suppressed', reason: 'collision' });
    }
  }
  // 输出保持输入序（消费方按 id 取用）
  const byId = new Map(placements.map((p) => [p.id, p]));
  return inputs.map((f) => byId.get(f.id)!);
}

/** 成对重叠率（测试/验收指标）：已放置标签盒的两两重叠对数 / C(n,2)。 */
export function pairwiseOverlapRate(
  inputs: GridLabelInput[],
  placements: GridPlacement[],
): number {
  const placedBoxes: Box[] = [];
  const byId = new Map(placements.map((p) => [p.id, p]));
  for (const f of inputs) {
    const p = byId.get(f.id);
    if (!p || p.status !== 'placed') continue;
    const { w, h } = estimateLabelBoxEm(f.text);
    placedBoxes.push(boxFrom(f.x + p.dx, f.y + p.dy, w * f.fontSize, h * f.fontSize, 0, f.kind));
  }
  let pairs = 0;
  let overlapsN = 0;
  for (let i = 0; i < placedBoxes.length; i += 1) {
    for (let j = i + 1; j < placedBoxes.length; j += 1) {
      pairs += 1;
      if (overlaps(placedBoxes[i], placedBoxes[j])) overlapsN += 1;
    }
  }
  return pairs ? overlapsN / pairs : 0;
}

/** 无碰撞放置（V10 基线语义：全部放于锚点右上，不做避让）。 */
export function placeAllWithoutCollision(
  inputs: GridLabelInput[],
): GridPlacement[] {
  return inputs.map((f) => {
    if (!f.text.trim() || f.fontSize <= 0) {
      return { id: f.id, dx: 0, dy: 0, status: 'suppressed' as const, reason: 'empty_text' as const };
    }
    const offset = f.fontSize * 0.75;
    return { id: f.id, dx: offset, dy: offset, status: 'placed' as const, reason: '' as const };
  });
}

// ── W4.5 前端字段兜底（后端 label_plan 不可用时）────────────────────────

/** C3 缺省字段词表（与后端 NAME_EXACT_VOCAB 高频子集对齐；扫描序即优先序）。 */
const FALLBACK_FIELD_VOCAB = ['name', 'name_zh', 'title', 'label', '代号', '名称'];

export interface FallbackFieldChoice {
  field: string;
  /** true = 后端 label_plan 不可用，前端按 C3 缺省策略自选（诚实降级标记）。 */
  degraded: true;
}

/**
 * 前端字段兜底（W4.5）：后端 label_plan 缺失时按 C3 缺省词表自选字段。
 * 扫描序确定性；候选字段值全空的也算命中（诚实）但优先有值字段。
 */
export function pickLabelField(
  features: Array<Record<string, unknown> | undefined | null>,
): FallbackFieldChoice | null {
  if (!features.length) return null;
  const keys = new Set<string>();
  for (const f of features) {
    if (!f) continue;
    for (const k of Object.keys(f)) keys.add(k);
  }
  let best: { field: string; nonEmpty: number } | null = null;
  for (const field of FALLBACK_FIELD_VOCAB) {
    if (!keys.has(field)) continue;
    let nonEmpty = 0;
    for (const f of features) {
      const v = f?.[field];
      if (typeof v === 'string' && v.trim()) nonEmpty += 1;
      else if (typeof v === 'number' && Number.isFinite(v)) nonEmpty += 1;
    }
    if (!best || nonEmpty > best.nonEmpty) best = { field, nonEmpty };
    if (nonEmpty === features.length) break; // 满值命中提前收（扫描序优先）
  }
  return best ? { field: best.field, degraded: true } : null;
}
