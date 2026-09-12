/**
 * required_components_for（前端镜像）—— 缺项主动补全（AC-07 / ADR-0156 §P2）。
 *
 * 规则单一语义源在后端 component_composer.required_components_for（评审
 * 对账侧）；本模块是 live/export 渲染管线的执行镜像 —— 输入/输出与后端
 * 同构（RequiredComponentsPlan 形），规则表由双侧单测锁定一致。
 *
 * 替代 `__fallback_*` 被动注入：主动补全 + 记录 CompositionDecision；
 * fallback 路径保留为安全网（autofill 之后仍缺 → fallback 命中 → 计数）。
 */

import type { CompositionDecision, PageProfile } from './composition-descriptor';

export const OUTPUT_PURPOSES: readonly PageProfile[] = [
  'screen_16_9', 'screen_4_3', 'a4_portrait', 'a4_landscape',
];

export interface ContentFlags {
  /** spec 携带投影/坐标系信息。 */
  has_projection_info: boolean;
  /** attribution 已有真实署名文本。 */
  has_data_source: boolean;
  /** 存在专题渲染层（需要图例）。 */
  has_thematic_layer: boolean;
  /** 已有统计面板（组件族在场）。 */
  has_statistics_panel: boolean;
  /** 研究区区位语境（需要插图）。 */
  has_location_context: boolean;
}

export interface RequiredComponentRule {
  type: string;
  reason: string;
  advisory?: string;
  placeholderOptions?: Record<string, unknown>;
}

export interface RequiredComponentsPlan {
  purpose: PageProfile;
  required: RequiredComponentRule[];
  missing: string[];
  advisories: string[];
  decisions: CompositionDecision[];
}

const DATA_SOURCE_PLACEHOLDER = '数据来源：—（待补充）';

const _SCREEN_PURPOSES: ReadonlySet<string> = new Set(['screen_16_9', 'screen_4_3']);

/** 规则表（与后端 required_components_for 同步 —— 改此必须双侧同步 + 单测锁定）。 */
export function requiredComponentsFor(
  purpose: string,
  content: Partial<ContentFlags>,
): RequiredComponentsPlan {
  const resolvedPurpose: PageProfile =
    (OUTPUT_PURPOSES as readonly string[]).includes(purpose)
      ? (purpose as PageProfile)
      : 'screen_16_9';
  const isPrint = !_SCREEN_PURPOSES.has(resolvedPurpose);
  const plan: RequiredComponentsPlan = {
    purpose: resolvedPurpose,
    required: [],
    missing: [],
    advisories: [],
    decisions: [],
  };
  const add = (rule: RequiredComponentRule) => plan.required.push(rule);

  add({ type: 'title', reason: '图名必配（全用途基线）' });
  add({ type: 'scale_bar', reason: '比例尺必配（全用途基线）' });
  add({ type: 'north_arrow', reason: '指北针必配（全用途基线）' });
  if (content.has_data_source) {
    add({ type: 'attribution', reason: '数据署名必配（全用途基线）' });
  } else {
    add({
      type: 'attribution',
      reason: '数据署名必配（全用途基线）',
      advisory: '数据来源未知：已补占位署名，禁止省略（§0.5）',
      placeholderOptions: { text: DATA_SOURCE_PLACEHOLDER },
    });
    plan.missing.push('data_source');
    plan.advisories.push('数据来源未知：attribution 以「数据来源：—（待补充）」占位');
  }
  if (content.has_thematic_layer) {
    add({ type: 'legend', reason: '专题层在场 → 图例必配' });
  }
  if (isPrint) {
    if (content.has_projection_info) {
      add({ type: 'graticule', reason: '印刷品投影信息在场 → 经纬网必配' });
    } else {
      plan.advisories.push(
        '缺投影信息：经纬网降级为经纬网 alone（角注记省略），比例尺不省略（§0.5）');
    }
    if (content.has_location_context) {
      add({ type: 'inset_map', reason: '区位语境在场 → 位置插图必配（印刷品）' });
    }
  }
  let step = 0;
  for (const rule of plan.required) {
    plan.decisions.push({
      step, kind: 'autofill', componentId: '', componentType: rule.type,
      after: 'present', reason: rule.reason,
    });
    step += 1;
  }
  return plan;
}

export const DATA_SOURCE_PLACEHOLDER_TEXT = DATA_SOURCE_PLACEHOLDER;

/** 从组件类型集合提取 content flags（就近投影，不做深层 spec 解析）。 */
export function contentFlagsFrom(
  types: ReadonlySet<string>,
  hasProjectionInfo: boolean,
): ContentFlags {
  return {
    has_projection_info: hasProjectionInfo,
    has_data_source: types.has('attribution'),
    has_thematic_layer: types.has('legend') || types.has('categorical_legend')
      || types.has('continuous_colorbar'),
    has_statistics_panel: types.has('statistics_panel'),
    has_location_context: types.has('inset_map'),
  };
}

/**
 * 缺项补全执行器：现役组件类型集合 vs 必配清单 → 需要新增的组件实例
 * （带 `__autofill_` 前缀 id —— provenance 可审计，区别于用户 spec 与
 * `__fallback_` 安全网）。attribution 占位：缺真实署名且组件缺席时不
 * 凭空造组件之外的信息 —— 占位文本经 placeholderOptions 携带。
 */
export function autofillComponents(
  purpose: string,
  existingTypes: ReadonlySet<string>,
  content: Partial<ContentFlags>,
): Array<RequiredComponentRule & { id: string }> {
  const plan = requiredComponentsFor(purpose, content);
  const out: Array<RequiredComponentRule & { id: string }> = [];
  for (const rule of plan.required) {
    if (existingTypes.has(rule.type)) continue;
    out.push({
      ...rule,
      id: `__autofill_${rule.type}`,
      placeholderOptions: rule.placeholderOptions ?? undefined,
    });
  }
  return out;
}
