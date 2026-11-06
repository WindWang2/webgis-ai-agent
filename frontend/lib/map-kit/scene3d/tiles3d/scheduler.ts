/**
 * scene3d/tiles3d/scheduler — SSE 有界调度决策（G3D02 M6）。
 *
 * 纯函数（零 IO/零 WebGL）：给定 tile 树 + 相机态 + 预算，产出
 * {render, load, unload, degraded}。管理器只负责执行（fetch/cancel/dispose）。
 *
 * 语义：
 * - SSE ≤ 预算或无子 → 选定该 tile（ADD：同时继续下钻；REPLACE：终止）。
 * - REPLACE 细化且选定后代未全就绪 → 兜底渲染「最近的就绪祖先」：本层
 *   内容就绪则后代全部退场（REPLACE = 祖先内容被替换，父子同屏是几何
 *   重叠 z-fighting，兄弟 404/预算退避会让重叠永久化）；本层未就绪则
 *   只排队加载、保留子树内已就绪部分覆盖（部分覆盖优于空洞），祖先
 *   就绪时由其分支压制本子树整体。
 * - 视锥外的子树整体跳过（不渲染、不加载）。
 * - 预算（maxVisibleTiles）耗尽 → 停止下钻保持粗 LOD（诚实降级计数）。
 * - load = 选定集中未就绪者（遍历优先序）；unload = 上 tick 差集。
 */

import type { TileNode } from './tileset';

export interface SchedulerCamera {
  /** 相机在锚点 ENU（米）中的位置。 */
  eyeEnu: { x: number; y: number; z: number };
  /** 视锥平面（ENU 米制，法向外指；null = 不剔除）。 */
  frustumPlanes: { normal: { x: number; y: number; z: number }; d: number }[] | null;
  viewportHeightPx: number;
  fovRad: number;
}

export interface SchedulerPolicy {
  maxSse: number;
  maxVisibleTiles: number;
}

export interface SchedulerDecision {
  /** 本 tick 应渲染的 tile id 集（含未加载 —— 管理器据此排队）。 */
  render: Set<string>;
  /** 需要加载的 tile id（render 集中未就绪的，优先序 = 遍历序）。 */
  load: string[];
  /** 不再需要（相对上 tick）。 */
  unload: string[];
  /** 降级计数（预算截断 / 剔除跳过），观察披露面。 */
  degraded: { budgetCappedTiles: number; frustumCulled: number };
}

function sseOf(tile: TileNode, cam: SchedulerCamera): number {
  const dx = cam.eyeEnu.x - tile.sphereEnu.cx;
  const dy = cam.eyeEnu.y - tile.sphereEnu.cy;
  const dz = cam.eyeEnu.z - tile.sphereEnu.cz;
  const dist = Math.max(Math.hypot(dx, dy, dz) - tile.sphereEnu.radiusM, 0.001);
  const denom = dist * 2 * Math.tan(cam.fovRad / 2);
  return (tile.geometricError * cam.viewportHeightPx) / Math.max(denom, 1e-9);
}

function sphereOutsideFrustum(
  s: { cx: number; cy: number; cz: number; radiusM: number },
  planes: { normal: { x: number; y: number; z: number }; d: number }[],
): boolean {
  for (const p of planes) {
    const dist = p.normal.x * s.cx + p.normal.y * s.cy + p.normal.z * s.cz + p.d;
    if (dist < -s.radiusM) return true;
  }
  return false;
}

/**
 * 单次调度决策。`loadedSet` = 当前已就绪 tile id；`prevRender` = 上 tick
 * render 集。纯函数无副作用。
 */
export function scheduleTiles(
  root: TileNode,
  cam: SchedulerCamera,
  policy: SchedulerPolicy,
  loadedSet: ReadonlySet<string>,
  prevRender: ReadonlySet<string>,
): SchedulerDecision {
  const render = new Set<string>();
  const load: string[] = [];
  let budgetCapped = 0;
  let culled = 0;

  const select = (tile: TileNode): boolean => {
    if (tile.contentUri === null || render.has(tile.id)) return true;
    if (render.size >= policy.maxVisibleTiles) {
      budgetCapped += 1;
      return false;
    }
    render.add(tile.id);
    if (!loadedSet.has(tile.id)) load.push(tile.id);
    return true;
  };

  /**
   * 撤下子树中「已就绪」的后代（会与祖先兜底同屏重叠）；未就绪后代保留在
   * render/load —— 撤下加载请求会让兜底永久化（全齐切换永不到来）。
   */
  const suppressSubtree = (tile: TileNode): void => {
    for (const child of tile.children) {
      // Load requests have their own list and remain queued. Pending render
      // placeholders must also yield their slots to the ready ancestor.
      render.delete(child.id);
      suppressSubtree(child);
    }
  };

  /**
   * @returns ready = 子树在期望 LOD 上全部就绪；covered = 子树内已有
   * 可渲染内容（REPLACE 兜底选路依据：祖先仅在本子树无覆盖时才上屏）。
   */
  const visit = (tile: TileNode): { ready: boolean; covered: boolean } => {
    if (cam.frustumPlanes && sphereOutsideFrustum(tile.sphereEnu, cam.frustumPlanes)) {
      culled += countSubtree(tile);
      return { ready: true, covered: true }; // 视锥外不参与"未就绪"语义
    }
    const sse = sseOf(tile, cam);
    const wantsRefine = sse > policy.maxSse && tile.children.length > 0;
    if (!wantsRefine) {
      const selected = select(tile);
      const ready = (selected && loadedSet.has(tile.id)) || tile.contentUri === null;
      return { ready, covered: ready };
    }
    if (tile.refine === 'ADD') {
      select(tile);
    }
    let descendantsReady = true;
    let covered = false;
    for (const child of tile.children) {
      const r = visit(child);
      if (!r.ready) descendantsReady = false;
      if (r.covered) covered = true;
    }
    if (tile.refine === 'REPLACE' && !descendantsReady) {
      // 渐进加载期兜底：最近就绪祖先上屏（不空窗）；父子同屏 = REPLACE
      // 语义违约（几何重叠 z-fighting），压制的后代由管理器缓存待全齐回屏。
      if (loadedSet.has(tile.id)) {
        suppressSubtree(tile);
        select(tile);
        return { ready: false, covered: true };
      }
      // 本层未就绪：排队加载但不压制子树 —— 子树内已就绪的部分继续覆盖
      //（部分覆盖优于空洞）；祖先就绪时由祖先分支压制本子树整体。
      select(tile);
      return { ready: false, covered };
    }
    return {
      ready: descendantsReady && (tile.refine === 'ADD' ? loadedSet.has(tile.id) : true),
      covered: covered || (tile.refine === 'ADD' ? loadedSet.has(tile.id) : false),
    };
  };

  visit(root);

  const unload: string[] = [];
  for (const id of prevRender) {
    if (!render.has(id)) unload.push(id);
  }
  return {
    render,
    load,
    unload,
    degraded: { budgetCappedTiles: budgetCapped, frustumCulled: culled },
  };
}

function countSubtree(tile: TileNode): number {
  let n = 1;
  for (const c of tile.children) n += countSubtree(c);
  return n;
}
