/**
 * CompositionDescriptor —— 版面描述中间层（AC-07 / ADR-0156）。
 *
 * 本线产出、08 线（导出画幅）消费的**单一语义源**：live chrome 与
 * canvas export 两侧的版面合成结果（元素落位 + 决策轨迹 + chrome 增益）
 * 都归一到这个可序列化结构，替代各自为政的隐式推导。
 *
 * 设计契约：
 * - version 化（当前 1）；字段 additive 演进，不破坏既有消费者；
 * - 纯数据：无函数、无 React 节点 —— 可进 artifact / 评审面板 / 回归基线；
 * - provenance 三值：`spec`（用户/Agent 显式声明）> `autofill`（本线
 *   required_components_for 主动补全）> `fallback`（安全网被动注入，
 *   P2 后命中率应趋零 —— 每次命中都是中间层的失败信号）；
 * - decisions 是可审计工件：09 线评审、10 线回归、后端 suggested_fix
 *   对账都消费同一结构（与 component_composer.CompositionDecision 同构）。
 */

export type CompositionOrigin = 'spec' | 'autofill' | 'fallback';

/** 版式档（画幅 × 横竖；与后端 layout_solver page profile 族对齐）。 */
export type PageProfile =
  | 'screen_16_9'
  | 'screen_4_3'
  | 'a4_portrait'
  | 'a4_landscape';

/** 修复策略链动作词表（与后端 REPAIR_ACTIONS 同表 —— 双侧锁定）。 */
export type RepairActionKind =
  | 'change_anchor'
  | 'shrink'
  | 'collapse_to_overflow'
  | 'hide_lowest_priority';

export interface RepairStep {
  action: RepairActionKind;
  componentId: string;
  componentType: string;
  from?: string;
  to?: string;
  reason: string;
}

/** 一条版面决策（autofill / fallback 命中 / 修复 / 抑制）。 */
export interface CompositionDecision {
  step: number;
  kind: 'autofill' | 'fallback_hit' | 'repair' | 'suppress';
  componentId: string;
  componentType: string;
  before?: string;
  after?: string;
  reason: string;
}

/** 中间层元素：一个图面组件的最终落位裁决。 */
export interface CompositionElement {
  id: string;
  type: string;
  anchor: string;
  slot: { index: number; size: number };
  stackOffsetPx: number;
  origin: CompositionOrigin;
  /** 本元素经历过的修复（空 = 一次到位）。 */
  repairs: RepairStep[];
}

/** 数字比例尺（比率式 1:xx；P3）。 */
export interface NumericScaleInfo {
  /** 比率分母（1:N 的 N，四舍五入到 3 位有效数字）。 */
  ratio: number;
  /** 比率标签（如 "1:50,000"）。 */
  label: string;
  barMeters: number;
  barPx: number;
}

/** 真北/磁北偏角（P4；数据不可得 → approximate=true）。 */
export interface DeclinationInfo {
  /** 偏角（度；东正西负 —— 磁北相对真北）。 */
  degrees: number;
  approximate: boolean;
  label: string;
}

/** 经纬网配置（P5 密度自适应 + P4 图廓注记格式）。 */
export interface GraticuleConfig {
  /** 选定间隔（度）。 */
  intervalDeg: number;
  /** 网格线数（经度方向 / 纬度方向）。 */
  lineCountLng: number;
  lineCountLat: number;
  /** 图廓注记格式（随跨度自适应）。 */
  labelFormat: 'deg' | 'dm' | 'dms';
  /** 密度来源：explicit（用户/spec 指定）> adaptive（跨度自适应）> zoom（既有 zoom 表回退）。 */
  source: 'explicit' | 'adaptive' | 'zoom';
}

/** 中间层主体（version 1）。 */
export interface CompositionDescriptor {
  version: 1;
  page: {
    profile: PageProfile;
    /** 画布逻辑尺寸（未知时缺省 —— 屏幕档默认 16:9 推断，§0.5）。 */
    width?: number;
    height?: number;
  };
  elements: CompositionElement[];
  decisions: CompositionDecision[];
  chrome: {
    numericScale?: NumericScaleInfo;
    declination?: DeclinationInfo;
    graticule?: GraticuleConfig;
  };
}

/** 从画布尺寸推断版式档（§0.5：画布未知 → 屏幕 16:9）。 */
export function pageProfileFor(
  width?: number,
  height?: number,
  paperSize?: string,
): PageProfile {
  if (paperSize === 'a4' && (width ?? 0) > (height ?? 0)) return 'a4_landscape';
  if (paperSize === 'a4') return 'a4_portrait';
  if (!width || !height || width <= 0 || height <= 0) return 'screen_16_9';
  const ratio = width / height;
  if (ratio > 1.2) return 'screen_16_9';
  return 'screen_4_3';
}
