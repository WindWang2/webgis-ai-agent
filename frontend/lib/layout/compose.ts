/**
 * composeMapLayout —— live 版面合成编排（AC-07 / ADR-0156 §P1+P2）。
 *
 * 渲染管线的前置纯函数：解析组件 → 缺项主动补全（P2）→ 冲突自愈
 * （P1 策略链）→ 产出可渲染列表 + CompositionDescriptor（版面描述
 * 中间层）。安全网（W5 起为 autofill 主动补全，`__fallback_*` 前缀退役；
 * 老工件 id 仍在 origin 映射中兼容读）：autofill 之后仍缺
 * chrome 族才触发 —— 每次命中都是补全规则的失败信号（计数进 decisions）。
 *
 * 诚实渲染边界：仅 chrome 族（scale_bar/north_arrow/attribution 占位）
 * 可自动注入 —— 数据承载件（title/legend/graticule/inset）无数据可填时
 * 注入即伪造，只进 advisory 决策（09 线评审可采纳）。修复链同理：
 * planCompositionRepairs 产出在 live 路径**执行**（W5 起，ADR-0165：
 * status='executed'；仅有未应用的动作如 shrink 保持 planned —— 工件不得
 * 声称未做的修复）。
 */

import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';
import {
  resolveMapComponent,
  resolveMapComponents,
} from '@/lib/map-components/resolve-components';
import {
  resolveSlotLayout,
} from '@/lib/map-components/layout-runtime';
import {
  stackOffsetPx,
} from '@/lib/map-components/resolve-layout';
import {
  pageProfileFor,
  type CompositionDescriptor,
  type CompositionDecision,
  type NumericScaleInfo,
  type PageProfile,
} from './composition-descriptor';
import {
  autofillComponents,
  DATA_SOURCE_PLACEHOLDER_TEXT,
} from './required-components';
import {
  planCompositionRepairs,
  repairStepsToDecisions,
} from './composition-repair';
import type { LayoutParticipant } from '@/lib/map-components/resolve-layout';
import { metersPerPixelAt } from '@/lib/map-kit/meters-per-pixel';
import { computeNiceScale } from '@/lib/map-kit/scale-math';
import { numericScaleAt } from './numeric-scale';
import { bboxCenter, magneticDeclinationAt } from './magnetic-declination';
import { selectGraticuleInterval } from './graticule-density';
import { graticuleLabelFormatForSpan } from './graticule-labels';

/** 可自动注入的类型（无需数据即可诚实渲染的 chrome 族）。 */
export const AUTOINJECTABLE_TYPES: ReadonlySet<string> = new Set([
  'scale_bar', 'north_arrow', 'attribution',
]);

export interface ComposeMapLayoutInput {
  components: MapSpecComponent[];
  spec: MapSpec | null;
  zoom: number;
  centerLat: number;
  bounds?: { west: number; south: number; east: number; north: number };
  /** dock 归属的组件（hasType 口径 —— 不参与渲染但算在场）。 */
  dockedTypes?: ReadonlySet<string>;
  /** 画布尺寸（未知 → 屏幕 16:9 默认，§0.5）。 */
  canvas?: { width: number; height: number };
  paperSize?: string;
}

export interface ComposeMapLayoutResult {
  renderable: MapSpecComponent[];
  descriptor: CompositionDescriptor;
  purpose: PageProfile;
}

function _presentTypes(
  components: MapSpecComponent[],
  dockedTypes?: ReadonlySet<string>,
): Set<string> {
  const types = new Set<string>();
  for (const c of components) {
    if (typeof c?.type === 'string' && c.type) types.add(c.type);
  }
  if (dockedTypes) for (const t of dockedTypes) types.add(t);
  return types;
}

export function composeMapLayout(input: ComposeMapLayoutInput): ComposeMapLayoutResult {
  const { components, zoom, centerLat, bounds, dockedTypes, canvas } = input;
  const purpose = pageProfileFor(canvas?.width, canvas?.height, input.paperSize);
  const present = _presentTypes(components, dockedTypes);

  const content = {
    has_projection_info: !!bounds,
    has_data_source: present.has('attribution'),
    has_thematic_layer: present.has('legend') || present.has('categorical_legend')
      || present.has('continuous_colorbar'),
    has_statistics_panel: present.has('statistics_panel'),
    has_location_context: present.has('inset_map'),
  };
  const wanted = autofillComponents(purpose, present, content);

  // V11 W5（G2，ADR-0165）：安全网从「特批 __fallback_* 直插渲染面」改为
  // **主动补全** —— 并入 autofill 候选流（同 id 规范、同注入路径、同 origin），
  // 决策以 kind='fallback_hit' 保留安全网审计语义；`__fallback_` 前缀归零。
  // 位置纪律（评审 finding）：并入必须在**注入循环之前** —— 否则 wanted
  // 不被消费，决策会声称 after:'present' 而渲染面什么也没有。
  const presentAfter = new Set(present);
  for (const item of wanted) {
    if (AUTOINJECTABLE_TYPES.has(item.type)) presentAfter.add(item.type);
  }
  const safetyNetTypes: Array<'north_arrow' | 'scale_bar'> = [];
  if (!presentAfter.has('north_arrow')) safetyNetTypes.push('north_arrow');
  if (!presentAfter.has('scale_bar')) safetyNetTypes.push('scale_bar');
  const safetyNetIds = new Set(safetyNetTypes.map((t) => `__autofill_${t}`));
  for (const type of safetyNetTypes) {
    wanted.push({
      id: `__autofill_${type}`, type,
      reason: '安全网主动补全（autofill 未覆盖）',
    });
  }

  const decisions: CompositionDecision[] = [];
  // 渲染面 = enabled 组件 + 补全/兜底件（presence 判定用全量 —— 显式
  // disabled 的类型不注入，『不要 compass』语义保持）。
  const renderable: MapSpecComponent[] = components.filter(
    (c) => !!c && typeof c === 'object' && c.enabled !== false,
  );
  let step = 0;
  for (const item of wanted) {
    const isSafetyNet = safetyNetIds.has(item.id);
    if (AUTOINJECTABLE_TYPES.has(item.type)) {
      const text = item.placeholderOptions?.['text'];
      renderable.push({
        id: item.id,
        type: item.type,
        enabled: true,
        ...(typeof text === 'string' ? { options: { text } } : {}),
      } as MapSpecComponent);
      decisions.push({
        step: step++,
        kind: isSafetyNet ? 'fallback_hit' : 'autofill',
        componentId: item.id,
        componentType: item.type, after: 'present',
        reason: item.reason,
      });
    } else {
      // 数据承载件：只记 advisory 决策（不凭空造）
      decisions.push({
        step: step++, kind: 'autofill', componentId: '',
        componentType: item.type, after: 'advisory_only',
        reason: `${item.reason}（数据承载件不自动造 —— 诚实渲染边界）`,
      });
    }
  }

  // 冲突自愈（P1）：锚定参与者上的策略链（user-pinned 不动）
  const repairParticipants: LayoutParticipant[] = resolveMapComponents({
    layout: { components: renderable },
  })
    .filter((c) => c.enabled && !c.floating && c.anchor !== 'none')
    .map((c) => ({
      id: c.id, type: c.type, anchor: c.anchor,
      floating: false, origin: 'auto' as const,
    }));
  const repair = planCompositionRepairs({
    participants: repairParticipants,
    canvas,
  });
  // V11 W5（G2，ADR-0165）：自愈从 planned → **executed** —— 四级策略链
  // （改锚 → 折叠 → 隐藏；shrink 由 placement 尺寸承载）应用到渲染面。
  // 决策记 status='executed'（工件如实声称已执行）；应用是确定性的
  // （anchorOverrides/collapseIds/hideIds 由 planCompositionRepairs 产出，
  // user-pinned 参与者本就不进链）。
  const appliedRepairIds = new Set<string>();
  for (let idx = 0; idx < renderable.length; idx += 1) {
    const c = renderable[idx];
    if (repair.anchorOverrides.has(c.id)) {
      renderable[idx] = {
        ...c,
        placement: {
          ...(c.placement ?? {}),
          mode: 'anchor' as const,
          anchor: repair.anchorOverrides.get(c.id),
        },
      } as MapSpecComponent;
      appliedRepairIds.add(c.id);
    } else if (repair.collapseIds.has(c.id)) {
      renderable[idx] = {
        ...c,
        placement: { ...(c.placement ?? {}), collapsed: true },
      } as MapSpecComponent;
      appliedRepairIds.add(c.id);
    } else if (repair.hideIds.has(c.id)) {
      renderable[idx] = { ...c, enabled: false } as MapSpecComponent;
      appliedRepairIds.add(c.id);
    }
  }
  // 逐动作状态（评审 finding）：仅**已应用**动作记 executed；未应用的
  // 动作（shrink —— 尺寸收缩无独立渲染字段承载）保持 planned，工件
  // 不声称未做的修复。
  decisions.push(...repairStepsToDecisions(
    repair.steps, step, 'planned',
    (s) => (appliedRepairIds.has(s.componentId) && s.action !== 'shrink'
      ? 'executed' : 'planned'),
  ));
  step += repair.steps.length;

  // chrome 增益（中间层 chrome 段 —— export 侧只读消费）
  const mpp = metersPerPixelAt(zoom, centerLat);
  const nice = computeNiceScale(mpp, 100);
  const numericScale: NumericScaleInfo = {
    ...numericScaleAt(zoom, centerLat),
    barMeters: nice.meters,
    barPx: nice.px,
  };
  const declination = bounds
    ? magneticDeclinationAt(bboxCenter(bounds).lat, bboxCenter(bounds).lng)
    : undefined;
  const graticuleComponent = renderable.find((c) => c.type === 'graticule');
  const explicitInterval = graticuleComponent
    ? (graticuleComponent.options as Record<string, unknown> | undefined)?.['interval']
    : undefined;
  const graticuleCfg = selectGraticuleInterval(
    bounds ? Math.max(0, bounds.east - bounds.west) : 0,
    bounds ? Math.max(0, bounds.north - bounds.south) : 0,
    {
      explicitIntervalDeg: typeof explicitInterval === 'number'
        ? explicitInterval : undefined,
      zoom,
    },
  );
  const graticule = bounds
    ? {
        ...graticuleCfg,
        labelFormat: graticuleLabelFormatForSpan(
          Math.max(bounds.east - bounds.west, bounds.north - bounds.south),
        ),
      }
    : undefined;

  const descriptor: CompositionDescriptor = {
    version: 1,
    page: { profile: purpose, ...(canvas ?? {}) },
    elements: (() => {
      // 槽位裁决走共享求解器（与渲染同一语义源 —— 非占位常量）
      const anchored = resolveMapComponents({ layout: { components: renderable } })
        .filter((c) => c.enabled && !c.floating && c.anchor !== 'none');
      const entries = anchored.map((c) => ({ type: c.type, anchor: c.anchor }));
      const solvedSlots = resolveSlotLayout(entries);
      const slotOf = new Map<string, { slot: string; index: number; slotSize: number; fallbackFrom?: string }>();
      entries.forEach((entryObj, i) => {
        const solved = solvedSlots.get(entryObj);
        if (solved) slotOf.set(anchored[i].id, solved);
      });
      return renderable.map((c) => {
        const resolvedItem = resolveMapComponent(c);
        const solved = slotOf.get(c.id);
        return {
          id: c.id,
          type: c.type,
          anchor: resolvedItem.anchor,
          slot: solved
            ? { index: solved.index, size: solved.slotSize }
            : { index: 0, size: 1 },
          stackOffsetPx: solved
            ? stackOffsetPx(solved as Parameters<typeof stackOffsetPx>[0], c.type)
            : 0,
          origin: c.id.startsWith('__autofill_')
            ? 'autofill'
            : c.id.startsWith('__fallback_') ? 'fallback' : 'spec',
          repairs: repair.steps.filter((s) => s.componentId === c.id),
        };
      });
    })(),
    decisions,
    chrome: {
      numericScale,
      declination,
      graticule,
    },
  };

  return { renderable, descriptor, purpose };
}

export { DATA_SOURCE_PLACEHOLDER_TEXT };
