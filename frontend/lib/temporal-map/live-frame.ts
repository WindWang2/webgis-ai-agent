/**
 * temporal-map live-frame 桥（GOAL 03 M4）。
 *
 * 模块级单例（与 selection-store 同款模式）：当前「已请求帧」是 ephemeral
 * 播放态（相机 P-9 同语义），不进 MapSpec、不进 LLM context；map-panel
 * reconcile 消费 live 帧（播放中）或 spec cursor（静止时）。displayedAt
 * 只在 markLiveApplied（应用 + 观测后）更新 —— 门控真相在这里。
 */
import type { MapSpecTemporalConfig, TemporalFrame } from '@/lib/mapspec-compiler/types';
import { TemporalPlaybackController } from './controller';

export { TemporalPlaybackController, temporalMetrics, resetTemporalMetricsForTests } from './controller';

interface LiveState {
  frame: TemporalFrame | null;
  seq: number;
  /** 应用 + 观测后的 displayed 帧（UI 唯一可信时间）。 */
  displayed: TemporalFrame | null;
  generation: number;
}

const state: LiveState = { frame: null, seq: 0, displayed: null, generation: 0 };
const listeners = new Set<() => void>();

/** 活动控制器（组件挂载注册；卸载/会话切换注销）。 */
export let activeController: TemporalPlaybackController | null = null;

export function setActiveController(controller: TemporalPlaybackController | null): void {
  if (activeController && activeController !== controller) {
    activeController.destroy();
  }
  activeController = controller;
  if (!controller) {
    state.frame = null;
    state.displayed = null;
    bump();
  }
}

function bump(): void {
  state.generation += 1;
  for (const fn of listeners) fn();
}

export function subscribeLiveTemporal(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getLiveTemporalGeneration(): number {
  return state.generation;
}

/** 当前应渲染的帧：live 帧（播放/拖动中）> null（静止时 map-panel 用 spec cursor）。 */
export function getLiveTemporalFrame(): TemporalFrame | null {
  return state.frame;
}

/** 当前 live 请求（frame + seq 令牌）—— map-panel 应用后回 markLiveApplied。 */
export function getLiveTemporalRequest(): { frame: TemporalFrame; seq: number } | null {
  return state.frame ? { frame: state.frame, seq: state.seq } : null;
}

/** displayed 帧（门控后；UI 时间标签唯一来源）。 */
export function getDisplayedTemporalFrame(): TemporalFrame | null {
  return state.displayed;
}

/**
 * 请求切帧（组件/播放 advance 调用）：经活动控制器取 seq 令牌并广播
 * live 帧 —— map-panel reconcile 随 generation 变化重跑并应用过滤。
 */
export function requestLiveFrame(frame: TemporalFrame): void {
  const controller = activeController;
  if (!controller) return;
  const req = controller.requestFrame(frame);
  state.frame = frame;
  state.seq = req.seq;
  bump();
}

/**
 * 应用 + 观测回执（map-panel 在 reconcile 落定后调用）。
 * 乱序/stale 由控制器拒绝；只有最新应用会推进 displayed。
 */
export function markLiveApplied(seq: number, frame: TemporalFrame): boolean {
  const controller = activeController;
  if (!controller) return false;
  const ok = controller.markApplied(seq, frame);
  if (ok) {
    state.displayed = frame;
    bump();
  }
  return ok;
}

/** 会话切换清理（resetLiveState 同期调用）：停播 + 清态。 */
export function resetTemporalLiveState(): void {
  if (activeController) {
    activeController.destroy();
    activeController = null;
  }
  state.frame = null;
  state.displayed = null;
  bump();
}

/** 便捷：以 live 帧（或 cursor）解析应渲染帧 —— map-panel compose 消费。 */
export function resolveEffectiveFrame(
  config: MapSpecTemporalConfig,
): TemporalFrame | null {
  if (state.frame && state.frame.id) return state.frame;
  const frames = config.frames || [];
  const idx = frames.findIndex((f) => f.id === config.cursor_frame);
  return idx >= 0 ? frames[idx] : frames[0] ?? null;
}
