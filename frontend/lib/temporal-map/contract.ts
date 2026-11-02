/**
 * temporal-map contract 读取面（GOAL 03 M4）。
 *
 * committed MapSpec.temporal 是时态唯一真相（后端 temporal_products 服务
 * 写入；前端只读 + 用户面 CAS 提交）。本模块提供纯选择器：无 temporal /
 * 未启用 → null（时间轴不渲染 —— 「无数据不伪造图层」）。
 */
import type {
  MapSpec,
  MapSpecTemporalConfig,
  TemporalFrame,
} from '@/lib/mapspec-compiler/types';

export type { MapSpecTemporalConfig, TemporalFrame };

export interface TemporalView {
  config: MapSpecTemporalConfig;
  frames: TemporalFrame[];
  cursorIndex: number;
  cursorFrame: TemporalFrame;
  /** 缺期披露（播放提示；missing_period_gap / single_period 等旗标）。 */
  quality: string[];
  missingHint: boolean;
}

/** committed spec → TemporalView（未绑定/未启用/坏游标 → null，绝不抛）。 */
export function readTemporalView(spec: MapSpec | null | undefined): TemporalView | null {
  const config = spec?.temporal;
  if (!config || !config.enabled) return null;
  const frames = Array.isArray(config.frames) ? config.frames : [];
  if (frames.length === 0) return null;
  const idx = frames.findIndex((f) => f.id === config.cursor_frame);
  const cursorIndex = idx >= 0 ? idx : 0;
  const quality = Array.isArray(config.quality) ? config.quality : [];
  return {
    config,
    frames,
    cursorIndex,
    cursorFrame: frames[cursorIndex],
    quality,
    missingHint: quality.includes('missing_period_gap') || quality.includes('irregular_spacing'),
  };
}

/** 帧标签序列（时间轴刻度；契约保证按 start 升序）。 */
export function frameLabels(view: TemporalView): string[] {
  return view.frames.map((f) => f.label);
}
