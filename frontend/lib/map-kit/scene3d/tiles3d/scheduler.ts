/**
 * scene3d/tiles3d/scheduler — SSE 有界调度决策（G3D02 M6）。
 *
 * 纯函数（零 IO/零 WebGL）：给定 tile 树 + 相机态 + 预算，产出
 * {render, load, unload, degraded}。管理器只负责执行（fetch/cancel/dispose）。
 *
 * 语义：
 * - SSE ≤ 预算或无子 → 选定该 tile（ADD：同时继续下钻；REPLACE：终止）。
 * - REPLACE 细化且选定后代未就绪 → 祖先兜底入选（渐进加载期不空窗）。
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

  const select = (tile: TileNode): void => {
    if (tile.contentUri === null) return;
    if (render.size >= policy.maxVisibleTiles) {
      budgetCapped += 1;
      return;
    }
    render.add(tile.id);
    if (!loadedSet.has(tile.id)) load.push(tile.id);
  };

  /** @returns 子树选定的后代是否全部就绪（REPLACE 祖先兜底判据）。 */
  const visit = (tile: TileNode): boolean => {
    if (cam.frustumPlanes && sphereOutsideFrustum(tile.sphereEnu, cam.frustumPlanes)) {
      culled += countSubtree(tile);
      return true; // 视锥外不参与"未就绪"语义
    }
    const sse = sseOf(tile, cam);
    const wantsRefine = sse > policy.maxSse && tile.children.length > 0;
    if (!wantsRefine) {
      select(tile);
      return loadedSet.has(tile.id) || tile.contentUri === null;
    }
    if (tile.refine === 'ADD') {
      select(tile);
    }
    let descendantsReady = true;
    for (const child of tile.children) {
      if (!visit(child)) descendantsReady = false;
    }
    if (tile.refine === 'REPLACE' && !descendantsReady) {
      // 渐进加载期：子孙未就绪 → 祖先兜底渲染（不空窗）。
      select(tile);
      return false;
    }
    return descendantsReady && (tile.refine === 'ADD' ? loadedSet.has(tile.id) : true);
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
