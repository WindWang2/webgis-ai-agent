import { useHudStore } from '@/lib/store/useHudStore';
import { mergePendingPresentation, getCommittedMapSpec } from '@/lib/mapspec/session-cursor';

/**
 * 「地图随对话」——轮次作用域的图层聚焦（2026-08-26 用户反馈）。
 *
 * 多轮会话里每轮分析各自挂载图层，历史轮次的图层全部保持可见会把地图
 * 堆成大杂烩（上一轮的热力图叠着这一轮的区县专题图）。契约：
 *  - 每个分析图层记 `_displayTurn`（它最后一次被"显示"时所属的对话轮次）；
 *  - Agent 在当前轮展示图层（display_layer / runtime_patch visible 挂载）时，
 *    把仍可见、`_displayTurn` 落后于当前轮的分析图层收起（HUD visible +
 *    pending presentation，与图层面板眼睛开关同机制 #737）——地图切到
 *    当前对话主题；
 *  - 用户手动点开的图层只标记轮次、不触发收起（不与用户对抗）；同轮内
 *    Agent 后续展示的图层（同轮标记）不被收起。
 * 轮次计数跨会话单调递增（无重置）：会话切换后恢复的存量层无标记（视为
 * 第 0 轮），新会话首次展示同样让位——与新会话聚焦语义一致。
 */

let turn = 0;

export function currentTurn(): number {
  return turn;
}

/** 新对话轮次（用户发送消息时调用）。 */
export function nextTurn(): number {
  turn += 1;
  return turn;
}

export function resetTurnFocusForTests(): void {
  turn = 0;
}

/** 行政边界层（context_role=boundary）默认常显：主题切换/收口不收起。 */
function isBoundaryContextLayer(specLayerId: string | undefined): boolean {
  if (!specLayerId) return false;
  const specLayer = (getCommittedMapSpec()?.layers || []).find(
    (l) => l.id === specLayerId,
  ) as unknown as { context_role?: string } | undefined;
  return specLayer?.context_role === 'boundary';
}

function parkStaleLayers(exceptLayerId: string): void {
  const { layers, updateLayer } = useHudStore.getState();
  for (const layer of layers ?? []) {
    if (layer.id === exceptLayerId) continue;
    // group 缺失（旧路径恢复的层）按 analysis 处理；只排除显式的 base 等组。
    if ((layer.group ?? 'analysis') !== 'analysis' || !layer.visible) continue;
    // 未标记（会话恢复的存量层）视为第 0 轮 —— 新轮展示时同样让位。
    const displayTurn = layer._displayTurn ?? 0;
    if (displayTurn >= turn) continue;
    // 旧路径恢复层的 id 本身就是 MapSpec 层 id（无 _mapspecLayerId）——
    // 兜底用 layer.id，否则 committed spec 层的收起不生效（#737 变体）。
    const specLayerId = layer._mapspecLayerId ?? layer.id;
    if (isBoundaryContextLayer(specLayerId)) continue;
    if (specLayerId) {
      mergePendingPresentation(specLayerId, { visible: false });
    }
    updateLayer(layer.id, { visible: false });
  }
}

/**
 * Agent 展示语义：标记当前轮并收起旧轮可见层。调用方负责/已完成该层
 * 自身的 visible 置位（display 命令、visible 挂载路径各自处理）。
 */
export function noteAgentDisplayed(layerId: string): void {
  const layer = useHudStore.getState().layers?.find((l) => l.id === layerId);
  if (!layer) return;
  useHudStore.getState().updateLayer(layerId, { _displayTurn: turn });
  parkStaleLayers(layerId);
}

/** 用户手动展示语义：只标记轮次，不触发收起；并 pin（finalize 不自动隐藏）。 */
export function tagUserDisplayed(layerId: string): void {
  const { layers, updateLayer } = useHudStore.getState();
  const layer = layers?.find((l) => l.id === layerId);
  if (!layer || layer.visible) return;
  updateLayer(layerId, { visible: true, _displayTurn: turn, _userPinned: true });
}

/** 用户手动隐藏 → 解除 pin（此后 Agent 收口语义恢复常态）。 */
export function untagUserPinned(layerId: string): void {
  const layer = useHudStore.getState().layers?.find((l) => l.id === layerId);
  if (!layer?._userPinned) return;
  useHudStore.getState().updateLayer(layerId, { _userPinned: false });
}

/** finalize/收口豁免：用户手动点开且仍 pin 的层不自动隐藏（用户优先）。 */
export function isUserPinned(layerId: string): boolean {
  const layer = useHudStore.getState().layers?.find((l) => l.id === layerId);
  return Boolean((layer as { _userPinned?: boolean } | undefined)?._userPinned);
}
