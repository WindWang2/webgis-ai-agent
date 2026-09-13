/**
 * Label Geometry —— 标注几何公共原语（V11 W4，ADR-0164；与 W0.2 的
 * Python ``label_typography`` 单点化对齐）。
 *
 * 消费方：
 * - ``mapspec-compiler/label-solver.ts``（导出孪生，parity corpus 冻结）；
 * - ``mapspec-runtime/label-grid.ts``（交互侧网格碰撞）。
 *
 * 口径纪律：本模块是 TS 侧**唯一**的盒估算/CJK 判定/8 方位序/keep-upright
 * /AABB/格网实现 —— 两消费方此前各自复制（M3 评审 finding），收敛后由
 * parity corpus（label-solver 侧）与 box 口径断言（label-grid 侧）双向锁定。
 * 纯函数、无随机、无 locale。
 */

/** 点标注 8 方位候选序（Python DECLUTTER_OFFSETS 同表；右上最优）。 */
export const DECLUTTER_OFFSETS: ReadonlyArray<readonly [number, number]> = [
  [1, 1], [1, 0], [1, -1], [0, -1],
  [-1, -1], [-1, 0], [-1, 1], [0, 1],
];

export const CJK_RANGES: ReadonlyArray<readonly [number, number]> = [
  [0x3000, 0x303f], [0x3400, 0x4dbf], [0x4e00, 0x9fff],
  [0xf900, 0xfaff], [0xff00, 0xffef],
];

export function isCjk(ch: string): boolean {
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

/** 盒估算（em 单位；CJK 1.0 / 其他 0.6，高 1.2 —— 与 estimateLabelBox 同口径）。 */
export function estimateLabelBoxEm(text: string): { w: number; h: number } {
  if (!text) return { w: 0, h: 1.2 };
  let em = 0;
  for (const ch of text) em += isCjk(ch) ? 1.0 : 0.6;
  return { w: em, h: 1.2 };
}

/** AABB（x1, y1, x2, y2）。 */
export type Box = [number, number, number, number];

export function overlaps(a: Box, b: Box): boolean {
  // 严格重叠（贴边不算碰撞；与 Python _overlaps 同式）
  return a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
}

export function insideViewport(box: Box, vp: readonly number[]): boolean {
  return vp[0] <= box[0] && box[2] <= vp[2] && vp[1] <= box[1] && box[3] <= vp[3];
}

/** keep-upright（导出孪生语义；与 Python keep_upright_export_twin 逐分支等价，
 *  被 parity corpus 冻结 —— 135°→315.0 等取值不可单侧改）。 */
export function keepUpright(deg: number): number {
  let a = deg % 360;
  if (a > 180) a -= 360;
  else if (a <= -180) a += 360;
  if (a > 90 || a < -90) a = (a + 180) % 360;
  return a;
}

export function cornerBox(x: number, y: number, w: number, h: number): Box {
  return [x, y, x + w, y + h];
}

export function centeredBox(x: number, y: number, w: number, h: number, angleDeg: number): Box {
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

/** 均匀格网 AABB 索引（Python LabelGrid 同构；插入序确定性）。 */
export class SpatialGrid {
  private cells = new Map<string, Box[]>();

  constructor(private cell: number) {
    this.cell = cell > 0 ? cell : 1e-6;
  }

  private span(b: Box): [number, number, number, number] {
    return [
      Math.floor(b[0] / this.cell), Math.floor(b[2] / this.cell),
      Math.floor(b[1] / this.cell), Math.floor(b[3] / this.cell),
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
