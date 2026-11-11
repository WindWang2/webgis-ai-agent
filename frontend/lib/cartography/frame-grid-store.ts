/**
 * G25 画幅网格运行时 store（模块级单例 —— 与 temporal live-frame/
 * selection-store 同模式；不进 workbenchSlice，避免共享大文件补丁）。
 *
 * 持有纯 UI 运行时态：网格开合、活动画幅、同步维度、主图联动。真相
 * 分工：数据面（frames/组/active_frame 持久值）= committed MapSpec；
 * 本 store 只持有**会话内交互态**（optimistic 激活 + 同步开关）。
 * 激活同时经 commitFrameProductPatch 把 active_frame 落到服务端
 * （409 superseded 静默回灌 —— 刷新后从服务端真相恢复，A20）。
 */

export interface FrameGridRuntimeState {
  open: boolean;
  /** 运行时活动画幅（null = 无；网格打开时初始 = 产品 active_frame）。 */
  activeFrameId: string | null;
  syncPan: boolean;
  syncZoom: boolean;
  /** 主图 ↔ 活动画幅联动（默认开）。 */
  linkMain: boolean;
}

let state: FrameGridRuntimeState = {
  open: false,
  activeFrameId: null,
  syncPan: true,
  syncZoom: true,
  linkMain: true,
};

const listeners = new Set<() => void>();

function set(patch: Partial<FrameGridRuntimeState>): void {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export function getFrameGridSnapshot(): FrameGridRuntimeState {
  return state;
}

export function subscribeFrameGrid(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** 打开网格（activeFrameId 给定或沿用当前）。 */
export function openFrameGrid(activeFrameId?: string | null): void {
  set({ open: true, ...(activeFrameId !== undefined ? { activeFrameId } : {}) });
}

/** 关闭网格（活动画幅保留在 store —— 供 camera 草稿与再开一致性）。 */
export function closeFrameGrid(): void {
  set({ open: false });
}

/** 激活画幅（运行时即时生效；持久化由调用方经 commitFrameProductPatch）。 */
export function setActiveFrameRuntime(frameId: string | null): void {
  set({ activeFrameId: frameId });
}

export function setFrameGridSyncDimensions(syncPan: boolean, syncZoom: boolean): void {
  set({ syncPan, syncZoom });
}

export function setFrameGridLinkMain(linkMain: boolean): void {
  set({ linkMain });
}

/** 测试隔离。 */
export function resetFrameGridForTest(): void {
  state = {
    open: false, activeFrameId: null, syncPan: true, syncZoom: true, linkMain: true,
  };
  for (const l of listeners) l();
}
