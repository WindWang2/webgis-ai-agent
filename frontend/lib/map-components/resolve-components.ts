/**
 * Shared map-component resolution (ADR-0081 Export Parity).
 *
 * live chrome（MapSpecChrome/MapPanel）与 canvas exporter 此前各自独立解析
 * title/subtitle/placement/enabled/variant —— 两套解析在 title 槽位、比例尺
 * 算法、图例数据源、指北针旋转上持续漂移。本模块是两侧共用的**纯函数**
 * 组件解析层：输入 committed MapSpec（或 null），输出 normalized 组件模型。
 *
 * 契约：
 * - 不持有状态、不产生副作用（纯派生）；
 * - anchor 解析优先级：placement.mode=anchor 的 anchor > 旧 position 字段 >
 *   类型默认槽位（与 DEFAULT_POSITION 同表，不引入第二套默认值）；
 * - floating 组件保留像素坐标（viewport 像素语义），由消费端按各自坐标
 *   系换算（exporter 按画布/视口比例缩放）。
 */

import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';

/** 七槽锚点（与后端 components.Position 字面量一致）。 */
export type ChromeAnchor =
  | 'top-left'
  | 'top-center'
  | 'top-right'
  | 'bottom-left'
  | 'bottom-center'
  | 'bottom-right'
  | 'none';

/** 类型默认槽位 —— 与 live helpers.DEFAULT_POSITION 同表（单一默认值源）。 */
export const DEFAULT_COMPONENT_ANCHOR: Record<string, ChromeAnchor> = {
  title: 'top-center',
  subtitle: 'top-center',
  north_arrow: 'top-right',
  scale_bar: 'bottom-right',
  attribution: 'bottom-left',
  continuous_colorbar: 'bottom-right',
  legend: 'bottom-left',
  categorical_legend: 'bottom-left',
  annotation: 'top-left',
  statistics_panel: 'top-left',
  chart_panel: 'top-left',
};

/** 解析后的 normalized 组件模型（live 渲染与导出共同消费）。 */
export interface ResolvedMapComponent {
  /** 原始 spec 组件（options/style 原样携带）。 */
  component: MapSpecComponent;
  id: string;
  type: string;
  enabled: boolean;
  /** 解析后的锚点槽位（floating 组件此处为 anchor 兜底值）。 */
  anchor: ChromeAnchor;
  /** placement.mode === 'floating'。 */
  floating: boolean;
  /** floating 坐标（viewport 像素；仅 floating=true 时有意义）。 */
  floatingRect?: {
    x: number;
    y: number;
    width?: number;
    height?: number;
    zIndex?: number;
    collapsed?: boolean;
  };
  /** 文本型组件（title/subtitle/attribution/annotation）的 options.text。 */
  text: string;
  /** variant（options.variant > component.variant > ''）。 */
  variant: string;
  /** 图例/色条绑定的图层 id（options.layerId）。 */
  layerId: string;
  /**
   * 折叠态（mode 无关 —— ADR-0084 E-2 修复：此前 collapsed 只在 floating
   * 分支捕获，锚定面板的折叠在导出侧永远丢失）。
   */
  collapsed: boolean;
  options: Record<string, unknown>;
}

const VALID_ANCHORS: ReadonlySet<string> = new Set([
  'top-left',
  'top-center',
  'top-right',
  'bottom-left',
  'bottom-center',
  'bottom-right',
  'none',
]);

/** 单组件解析（placement > position > 类型默认，无第二套默认值）。 */
export function resolveMapComponent(component: MapSpecComponent): ResolvedMapComponent {
  const placement = component.placement;
  let floating = false;
  let floatingRect: ResolvedMapComponent['floatingRect'];

  if (placement?.mode === 'floating') {
    floating = true;
    floatingRect = {
      x: typeof placement.x === 'number' ? placement.x : 0,
      y: typeof placement.y === 'number' ? placement.y : 0,
      width: typeof placement.width === 'number' ? placement.width : undefined,
      height: typeof placement.height === 'number' ? placement.height : undefined,
      zIndex: typeof placement.zIndex === 'number' ? placement.zIndex : 40,
      collapsed: placement.collapsed === true,
    };
  }

  const anchorCandidate =
    placement?.mode === 'anchor' && typeof placement.anchor === 'string'
      ? placement.anchor
      : (component as unknown as { position?: string }).position;
  const anchor: ChromeAnchor = VALID_ANCHORS.has(anchorCandidate ?? '')
    ? (anchorCandidate as ChromeAnchor)
    : (DEFAULT_COMPONENT_ANCHOR[component.type] ?? 'none');

  const options = component.options ?? {};
  const textOpt = options['text'];
  const variantOpt = options['variant'];
  const layerIdOpt = options['layerId'];

  return {
    component,
    id: component.id,
    type: component.type,
    enabled: component.enabled !== false,
    anchor,
    floating,
    floatingRect,
    text: typeof textOpt === 'string' ? textOpt : '',
    variant:
      (typeof variantOpt === 'string' && variantOpt) ||
      (typeof component.variant === 'string' && component.variant) ||
      '',
    layerId: typeof layerIdOpt === 'string' ? layerIdOpt : '',
    collapsed: placement?.collapsed === true,
    options,
  };
}

/**
 * 解析整个 spec 的组件列表（enabled 过滤留给消费端 —— live 需要注入
 * fallback north/scale，export 需要区分"类型缺席走内置默认"）。
 */
export function resolveMapComponents(
  spec: { layout?: { components?: MapSpecComponent[] } } | null | undefined,
): ResolvedMapComponent[] {
  const raw = spec?.layout?.components;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((c): c is MapSpecComponent => !!c && typeof c === 'object' && typeof c.type === 'string')
    .map(resolveMapComponent);
}

/** 按类型取第一个 enabled 组件（title/subtitle 等单例语义）。 */
export function findEnabled(
  resolved: ResolvedMapComponent[],
  type: string,
): ResolvedMapComponent | undefined {
  return resolved.find((c) => c.type === type && c.enabled);
}

/** 按类型取第一个组件（无论 enabled —— 用于"类型在场但被禁用"判定）。 */
export function findOfType(
  resolved: ResolvedMapComponent[],
  type: string,
): ResolvedMapComponent | undefined {
  return resolved.find((c) => c.type === type);
}

/**
 * 导出侧视口→画布坐标换算：floating 坐标是 live 视口像素，导出画布尺寸
 * 不同 —— 按比例缩放（确定性，文档化于 ADR-0081）。
 */
export function scaleFloatingRect(
  rect: NonNullable<ResolvedMapComponent['floatingRect']>,
  viewport: { width: number; height: number },
  canvas: { width: number; height: number },
): { x: number; y: number; width?: number; height?: number } {
  const sx = viewport.width > 0 ? canvas.width / viewport.width : 1;
  const sy = viewport.height > 0 ? canvas.height / viewport.height : 1;
  return {
    x: rect.x * sx,
    y: rect.y * sy,
    width: rect.width !== undefined ? rect.width * sx : undefined,
    height: rect.height !== undefined ? rect.height * sy : undefined,
  };
}
