/**
 * temporal-map 数据面联动（GOAL 03 M4 —— Oracle 4 同窗保证）。
 *
 * 图表（viewport aggregate）/ 表格（paged features）的取数 where 由本模块
 * 从 committed temporal + live 帧派生 —— 与地图 layer.filter 用同一编译器
 * （filter.ts）与同一指纹：地图 2025 则图表/表格同 2025，静默错配在结构
 * 上不可达。ref 不匹配 temporal 绑定 → 不附加（非时序层不受影响）。
 */
import {
  getCommittedMapSpec,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { subscribeLiveTemporal } from './live-frame';
import type { MapSpecTemporalConfig } from '@/lib/mapspec-compiler/types';
import { getLiveTemporalFrame } from './live-frame';
import { temporalFingerprint, temporalWhereAst, type WhereAst } from './filter';

export interface TemporalWindowLink {
  /** 帧窗口 where AST（无绑定/无帧 → undefined）。 */
  where: WhereAst | undefined;
  /** 窗口身份键（effect dep；''=无绑定 —— 同帧重渲染不重取）。 */
  key: string;
}

/** temporal 绑定是否覆盖该 ref（source_ref 或层绑定 source_ref 匹配）。 */
function bindingCoversRef(config: MapSpecTemporalConfig, refId: string): boolean {
  if (config.source_ref === refId) return true;
  return (config.layers || []).some((b) => b.source_ref === refId);
}

/**
 * ref → 当前时间窗（committed temporal + live 帧解析）。
 * 纯读取（模块态），调用方把它放进 hook 的 effect deps（key 变化重取）。
 */
export function temporalWindowForRef(refId: string | undefined | null): TemporalWindowLink {
  if (!refId) return { where: undefined, key: '' };
  const config = getCommittedMapSpec()?.temporal;
  if (!config?.enabled || !bindingCoversRef(config, refId)) {
    return { where: undefined, key: '' };
  }
  const frames = config.frames || [];
  const live = getLiveTemporalFrame();
  const frame = live && frames.some((f) => f.id === live.id)
    ? live
    : frames.find((f) => f.id === config.cursor_frame);
  if (!frame) return { where: undefined, key: '' };
  return {
    where: temporalWhereAst(config, frame),
    key: temporalFingerprint(config, frame.id),
  };
}

/** 两 where AST → and 合成（搜索过滤 × 时间窗）。 */
export function mergeWhereAst(a: WhereAst | undefined, b: WhereAst | undefined): WhereAst | undefined {
  if (!a) return b;
  if (!b) return a;
  return { op: 'and', terms: [a, b] };
}

/** AST → 服务端 where 参数（JSON 串）。 */
export function whereParam(ast: WhereAst | undefined): string | undefined {
  return ast ? JSON.stringify(ast) : undefined;
}

/**
 * 时间窗身份的双源订阅（P1 修正）：live 帧（播放/拖动）与 committed spec
 * （agent seek 经 MapSpec commit —— 不 bump live generation）任一变化都
 * 通知 —— 图表/表格在 agent 光标路径上同样跟随换窗。
 */
export function subscribeTemporalWindow(listener: () => void): () => void {
  const offSpec = subscribeMapSpecLive(listener);
  const offLive = subscribeLiveTemporal(listener);
  return () => {
    offSpec();
    offLive();
  };
}
