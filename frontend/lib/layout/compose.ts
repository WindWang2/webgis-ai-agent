/**
 * composeMapLayout —— live 版面合成编排（AC-07 / ADR-0156 §P1+P2）。
 *
 * 渲染管线的前置纯函数：解析组件 → 缺项主动补全（P2）→ 冲突自愈
 * （P1 策略链）→ 产出可渲染列表 + CompositionDescriptor（版面描述
 * 中间层）。fallback（`__fallback_*`）保留为安全网：autofill 之后仍缺
 * chrome 族才触发 —— 每次命中都是补全规则的失败信号（计数进 decisions）。
 *
 * 诚实渲染边界：仅 chrome 族（scale_bar/north_arrow/attribution 占位）
 * 可自动注入 —— 数据承载件（title/legend/graticule/inset）无数据可填时
 * 注入即伪造，只进 advisory 决策（09 线评审可采纳）。
 */

import type { MapSpec, MapSpecComponent } from '@/lib/mapspec-compiler/types';
import {
  resolveMapComponent,
  resolveMapComponents,
} from '@/lib/map-components/resolve-components';
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

  const decisions: CompositionDecision[] = [];
  // 渲染面 = enabled 组件 + 补全/兜底件（presence 判定用全量 —— 显式
  // disabled 的类型不注入，『不要指南针』语义保持）。
  const renderable: MapSpecComponent[] = components.filter(
    (c) => !!c && typeof c === 'object' && c.enabled !== false,
  );
  let step = 0;
  for (const item of wanted) {
    if (AUTOINJECTABLE_TYPES.has(item.type)) {
      const text = item.placeholderOptions?.['text'];
      renderable.push({
        id: item.id,
        type: item.type,
        enabled: true,
        ...(typeof text === 'string' ? { options: { text } } : {}),
      } as MapSpecComponent);
      decisions.push({
        step: step++, kind: 'autofill', componentId: item.id,
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

  // 安全网：autofill 后仍缺 north/scale → 既有 fallback 兜底。presence
  // 口径 = 全量类型（含显式 disabled —— 『不要指南针』语义）∪ 本轮注入。
  const presentAfter = new Set(present);
  for (const item of wanted) {
    if (AUTOINJECTABLE_TYPES.has(item.type)) presentAfter.add(item.type);
  }
  const fallbackDecor: MapSpecComponent[] = [];
  if (!presentAfter.has('north_arrow')) {
    fallbackDecor.push({ id: '__fallback_north_arrow', type: 'north_arrow', enabled: true } as MapSpecComponent);
    decisions.push({
      step: step++, kind: 'fallback_hit', componentId: '__fallback_north_arrow',
      componentType: 'north_arrow', after: 'present',
      reason: '安全网兜底（autofill 未覆盖）',
    });
  }
  if (!presentAfter.has('scale_bar')) {
    fallbackDecor.push({ id: '__fallback_scale_bar', type: 'scale_bar', enabled: true } as MapSpecComponent);
    decisions.push({
      step: step++, kind: 'fallback_hit', componentId: '__fallback_scale_bar',
      componentType: 'scale_bar', after: 'present',
      reason: '安全网兜底（autofill 未覆盖）',
    });
  }
  renderable.push(...fallbackDecor);

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
  decisions.push(...repairStepsToDecisions(repair.steps, step));

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
    elements: renderable.map((c) => {
      const resolvedItem = resolveMapComponent(c);
      return {
        id: c.id,
        type: c.type,
        anchor: resolvedItem.anchor,
        slot: { index: 0, size: 1 },
        stackOffsetPx: 0,
        origin: c.id.startsWith('__autofill_')
          ? 'autofill'
          : c.id.startsWith('__fallback_') ? 'fallback' : 'spec',
        repairs: repair.steps.filter((s) => s.componentId === c.id),
      };
    }),
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
