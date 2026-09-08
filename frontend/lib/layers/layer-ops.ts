/**
 * Layer Workspace 命令层（Workbench V4 / Wave 2）—— 用户批量/复合操作的
 * 唯一收口。每个命令复用既有 mutation 通道（user-mutation CAS 串行链），
 * 不建第二写路径；本模块只做编排（快照 → 批量乐观态 → 逐层提交）。
 *
 * 锁定（lock）护栏语义：锁定层的显隐/删除/样式在 UI 层禁用；本模块的
 * 批量与 isolate 命令同样跳过锁定层 —— lock 是用户意图护栏（与
 * _userPinned 的 turn-focus 豁免同族）。
 */
import { useHudStore } from '@/lib/store/useHudStore';
import {
  toggleLayerAndCommit,
  setLayerOpacityAndCommit,
  commitLayerStyleAndCommit,
} from '@/lib/mapspec/user-mutation';
import { tagUserDisplayed } from '@/lib/chat/turn-focus';
import { ensureLayerData } from '@/lib/store/layer-data';
import { clearRefSourceFailed } from '@/lib/mapspec/ref-source-resolver';
import { useToastStore } from '@/components/ui/toast';
import { devOnly } from '@/lib/utils/logger';

function isLocked(layerId: string): boolean {
  return useHudStore.getState().lockedLayerIds.includes(layerId);
}

/**
 * 隔离（solo）：目标层保持可见，其余层按既有 toggle 通道隐藏（逐层落 CAS）；
 * 进入时在 workbench slice 记录可见性快照，clearIsolateAndCommit 恢复。
 * 锁定层跳过隐藏；切换新隔离目标前先退出上一个（快照不叠加）。
 */
export async function isolateLayerAndCommit(layerId: string): Promise<void> {
  const { layers, beginIsolate, isolatedLayerId } = useHudStore.getState();
  if (!layers.some((l) => l.id === layerId)) return;
  if (isolatedLayerId === layerId) return;
  if (isolatedLayerId != null) await clearIsolateAndCommit();

  const snapshot: Record<string, boolean> = {};
  for (const layer of layers) {
    if (layer.id === layerId) continue;
    if (layer.visible) snapshot[layer.id] = true;
  }
  beginIsolate(layerId, snapshot);
  tagUserDisplayed(layerId, true);
  for (const id of Object.keys(snapshot)) {
    if (isLocked(id)) continue;
    try {
      await toggleLayerAndCommit(id);
    } catch (err) {
      devOnly.warn('[layer-ops] isolate hide failed:', id, err);
    }
  }
}

/** 退出隔离：按快照恢复可见性（跳过已删除/已恢复/锁定的层）。 */
export async function clearIsolateAndCommit(): Promise<void> {
  const { isolatedLayerId, isolatedFrom, clearIsolate } = useHudStore.getState();
  if (isolatedLayerId == null) return;
  const snapshot = isolatedFrom ?? {};
  clearIsolate();
  for (const id of Object.keys(snapshot)) {
    if (isLocked(id)) continue;
    const layer = useHudStore.getState().layers.find((l) => l.id === id);
    if (!layer || layer.visible) continue; // 已被并发操作恢复/删除
    try {
      await toggleLayerAndCommit(id);
    } catch (err) {
      devOnly.warn('[layer-ops] un-isolate restore failed:', id, err);
    }
  }
}

/**
 * 批量显隐（scope: 全部 / 选中 / 组成员，由调用方解析 scope → ids）。
 * 目标态与当前一致的层跳过；每层独立走 toggle 通道（乐观 pending 合并 +
 * 串行 CAS），单层失败不影响其余层。
 */
export async function batchSetVisibility(
  scopeLayerIds: readonly string[],
  visible: boolean,
): Promise<void> {
  for (const id of scopeLayerIds) {
    // Review R1（MINOR-11）：循环内重读最新状态 —— 前一层 await 期间用户
    // 可能改动了本层可见性；用入口快照会做出错误方向的 toggle。
    const layer = useHudStore.getState().layers.find((l) => l.id === id);
    if (!layer || layer.visible === visible) continue;
    if (isLocked(id)) continue;
    try {
      if (visible) tagUserDisplayed(id, true);
      await toggleLayerAndCommit(id);
    } catch (err) {
      devOnly.warn('[layer-ops] batch visibility failed:', id, err);
    }
  }
}

/** 批量不透明度（scope: 选中 / 组成员）。锁定层跳过。 */
export async function batchSetOpacity(
  scopeLayerIds: readonly string[],
  opacity: number,
): Promise<void> {
  if (!Number.isFinite(opacity)) return;
  const clamped = Math.min(1, Math.max(0, opacity));
  for (const id of scopeLayerIds) {
    // Review R1（MINOR-11）：同 batchSetVisibility —— 循环内重读。
    const layer = useHudStore.getState().layers.find((l) => l.id === id);
    if (!layer || isLocked(id)) continue;
    if (Math.abs((layer.opacity ?? 1) - clamped) <= 1e-9) continue;
    try {
      await setLayerOpacityAndCommit(id, clamped);
    } catch (err) {
      devOnly.warn('[layer-ops] batch opacity failed:', id, err);
    }
  }
}

/**
 * 复制样式：把 fromLayer 的 style 投影到 toLayer（本地 store 立即生效；
 * spec 承载层再走 patch_layer_style 持久通道）。legend_spec 一并携带 ——
 * 样式与图例同源（分类渲染的 legend 由 style 派生）。
 */
export async function duplicateStyle(fromLayerId: string, toLayerId: string): Promise<void> {
  const state = useHudStore.getState();
  const from = state.layers.find((l) => l.id === fromLayerId);
  const to = state.layers.find((l) => l.id === toLayerId);
  if (!from?.style || !to) return;
  if (isLocked(toLayerId)) return;
  const style = { ...from.style };
  useHudStore.getState().updateLayer(toLayerId, { style, legend_spec: from.legend_spec });
  // spec 承载层：样式持久化（HUD-only 层走本地 runtime patch，无需提交）。
  if (to._mapspecLayerId && Object.keys(style).length > 0) {
    try {
      await commitLayerStyleAndCommit(toLayerId, layerStyleToPaint(style));
    } catch (err) {
      devOnly.warn('[layer-ops] duplicate style commit failed:', err);
    }
  }
}

/**
 * 粘贴样式（Layer Workspace 行内「复制样式/粘贴样式」流）：把剪贴板样式
 * 投影到目标层（本地 store 立即生效；spec 承载层走 patch_layer_style 持久
 * 通道）。与 duplicateStyle 同一条持久化纪律。
 */
export async function pasteStyle(
  style: Record<string, unknown>,
  toLayerId: string,
): Promise<void> {
  const state = useHudStore.getState();
  const to = state.layers.find((l) => l.id === toLayerId);
  if (!to) return;
  if (isLocked(toLayerId)) return;
  useHudStore.getState().updateLayer(toLayerId, { style: { ...style } });
  if (to._mapspecLayerId) {
    try {
      await commitLayerStyleAndCommit(toLayerId, layerStyleToPaint(style as Record<string, unknown>));
    } catch (err) {
      devOnly.warn('[layer-ops] paste style commit failed:', err);
    }
  }
}

/** LayerStyle → MapSpec paint 最小投影（与 layer-style-panel 同款字段族）。 */
function layerStyleToPaint(style: Record<string, unknown>): Record<string, unknown> {
  const paint: Record<string, unknown> = {};
  if (typeof style.color === 'string') paint.color = style.color;
  if (typeof style.strokeColor === 'string') paint.strokeColor = style.strokeColor;
  if (typeof style.strokeWidth === 'number') paint.strokeWidth = style.strokeWidth;
  if (typeof style.opacity === 'number') paint.opacity = style.opacity;
  if (typeof style.pointSize === 'number') paint.radius = style.pointSize;
  return paint;
}

/**
 * 单层重试：ref 承载层清除失败墓碑并重跑数据回填（此前重试只隐含在
 * 观测→修复回路里，无用户入口 —— 审计 02 能力矩阵「retry 无」项）。
 * 非 ref 层 no-op（无独立加载通道）。
 */
export async function retryLayerLoad(layerId: string): Promise<void> {
  const layer = useHudStore.getState().layers.find((l) => l.id === layerId);
  const refId = layer?._refId;
  if (!layer || !refId) return;
  clearRefSourceFailed(refId);
  try {
    await ensureLayerData(layerId, 'filter');
    useToastStore.getState().addToast(`图层「${layer.name}」已重新加载`, 'success');
  } catch (err) {
    useToastStore.getState().addToast(`图层「${layer.name}」重载失败`, 'error');
    devOnly.warn('[layer-ops] retry load failed:', err);
  }
}
