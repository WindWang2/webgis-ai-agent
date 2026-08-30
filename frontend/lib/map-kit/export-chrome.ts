/**
 * Export chrome — shared placement semantics for the canvas exporter (ADR-0081).
 *
 * live chrome 的 placement 语义（anchor 七槽 / floating 像素坐标）此前在
 * exporter 里完全没有对应物（全部硬编码固定槽）。本模块把
 * `resolveMapComponents`（live/export 共用解析层）的输出映射到导出画布：
 *
 * - anchor → 画布槽位矩形（margin + 槽内堆叠）；
 * - floating → 视口像素按画布/视口比例缩放（确定性换算）；
 * - 组件族绘制（title/subtitle/罗盘/比例尺/图例/色条/署名/统计卡/图表）
 *   从同一 ResolvedMapComponent 模型出发 —— 语义一致，不要求像素级相同。
 *
 * 无 spec 组件时（旧会话/无 committed spec）exporter 走 legacy 固定槽路径，
 * 行为不变 —— parity 路径只在 spec 存在时激活。
 */

import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import type { LegendSpec } from './types';
import type { LayoutStyle } from './layout-style';
import {
  DEFAULT_COMPONENT_ANCHOR,
  resolveMapComponents,
  scaleFloatingRect,
  type ChromeAnchor,
  type ResolvedMapComponent,
} from '@/lib/map-components/resolve-components';
import { resolveComponentLayout } from '@/lib/map-components/resolve-layout';
import { computeNiceScale, formatScaleLabel } from './scale-math';

export interface StatsPanelData {
  title?: string;
  items: Array<{ label?: string; value?: string | number; unit?: string }>;
}

export interface ChartPanelData {
  type: 'bar' | 'line' | 'pie' | 'scatter';
  title: string;
  data: Array<{ name: string; value?: number; x?: number; y?: number }>;
  x_label?: string;
  y_label?: string;
}

/** 导出侧组件元素（从 ResolvedMapComponent 派生，含画布坐标）。 */
export interface ExportChromeElement {
  kind: string;
  anchor: ChromeAnchor;
  /** floating 缩放后的画布坐标（anchor 元素为 undefined）。 */
  rect?: { x: number; y: number; width?: number; height?: number };
  /** 槽内堆叠序（ADR-0084 共享求解器；0 贴边 —— 消费端加 index×层距偏移）。 */
  stackIndex?: number;
  /** 槽内组件总数（≤1 时消费方无需偏移）。 */
  slotSize?: number;
  text?: string;
  /** 组件 variant（map_border 等变体驱动型组件）。 */
  variant?: string;
  legendSpec?: LegendSpec;
  stats?: StatsPanelData;
  chart?: ChartPanelData;
}

export interface ExportChromeModel {
  /** 有任何 spec chrome 元素时为 true（false → exporter 走 legacy 槽位）。 */
  fromSpec: boolean;
  title?: ExportChromeElement;
  subtitle?: ExportChromeElement;
  northArrow?: ExportChromeElement;
  scaleBar?: ExportChromeElement;
  legend?: ExportChromeElement;
  colorbar?: ExportChromeElement;
  attribution?: ExportChromeElement;
  border?: ExportChromeElement;
  /** P6：spec graticule 组件 enabled → 导出绘制经纬网（live 无渲染器）。 */
  graticuleEnabled?: boolean;
  panels: ExportChromeElement[];
}

export interface BuildExportChromeOptions {
  /** committed MapSpec（layout.components 的事实源）。 */
  spec: { layout?: { components?: MapSpecComponent[] } } | null | undefined;
  /** live 视口尺寸（floating 坐标缩放基准；<=0 时视为 1:1）。 */
  viewport: { width: number; height: number };
  /** 请求参数覆盖（title/subtitle 显式请求优先于 spec）。 */
  requestTitle?: string;
  requestSubtitle?: string;
  /** 图例/色条数据：layerId → legend_spec（来自 spec.layers）。 */
  legendSpecsByLayer: Record<string, LegendSpec>;
  /** HUD 发现的兜底图例（spec 无图例组件时使用）。 */
  fallbackLegendSpec?: LegendSpec;
  /** chartRef → ChartData 的异步加载器（大载荷走 session artifact）。 */
  loadChart?: (ref: string) => Promise<ChartPanelData | null>;
}

function _anchorOf(c: ResolvedMapComponent): ChromeAnchor {
  return c.anchor ?? DEFAULT_COMPONENT_ANCHOR[c.type] ?? 'none';
}

function _floatingRectOf(
  c: ResolvedMapComponent,
  viewport: BuildExportChromeOptions['viewport'],
  canvas: { width: number; height: number },
): ExportChromeElement['rect'] | undefined {
  if (!c.floating || !c.floatingRect) return undefined;
  const scaled = scaleFloatingRect(c.floatingRect, viewport, canvas);
  return scaled;
}

function _parseStats(raw: unknown): StatsPanelData | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const items = (raw as { items?: unknown }).items;
  if (!Array.isArray(items) || items.length === 0) return undefined;
  const parsed = items
    .filter((it): it is Record<string, unknown> => !!it && typeof it === 'object')
    .map((it) => ({
      label: typeof it['label'] === 'string' ? it['label'] : undefined,
      value:
        typeof it['value'] === 'string' || typeof it['value'] === 'number'
          ? it['value']
          : undefined,
      unit: typeof it['unit'] === 'string' ? it['unit'] : undefined,
    }))
    .filter((it) => it.label !== undefined || it.value !== undefined);
  if (parsed.length === 0) return undefined;
  const title = (raw as { title?: unknown }).title;
  return { title: typeof title === 'string' ? title : undefined, items: parsed };
}

function _parseChart(raw: unknown): ChartPanelData | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const r = raw as Record<string, unknown>;
  const type = r['type'];
  if (type !== 'bar' && type !== 'line' && type !== 'pie' && type !== 'scatter') {
    return undefined;
  }
  const data = r['data'];
  const title = r['title'];
  if (!Array.isArray(data) || data.length === 0 || typeof title !== 'string') {
    return undefined;
  }
  return {
    type,
    title,
    data: data as ChartPanelData['data'],
    x_label: typeof r['x_label'] === 'string' ? r['x_label'] : undefined,
    y_label: typeof r['y_label'] === 'string' ? r['y_label'] : undefined,
  };
}

/**
 * 构建导出 chrome 模型（异步：chartRef 可能需要拉取 session artifact）。
 * 纯派生 —— 不读 DOM、不碰 map 实例；画布尺寸由调用方传入。
 */
export async function buildExportChrome(
  opts: BuildExportChromeOptions,
  canvas: { width: number; height: number },
): Promise<ExportChromeModel> {
  const resolved = resolveMapComponents(opts.spec);
  // review P0：只有**可视**组件在场才走 chrome 路径 —— 仅携带 export_layout
  // 等非可视组件的 spec（#805 场景）不得把导出切到 chrome 路径（否则罗盘/
  // 比例尺/默认标题全部从 legacy 回退中消失）。E-10：enabled 过滤与 live
  // 的 hasSpecChrome 对齐 —— 只有禁用 title 的 spec 走 HUD chrome 栈。
  // graticule（终审 F4）：属于可视输出（导出经纬网），计入 fromSpec ——
  // graticule-only spec 也走 chrome 路径（fallback 罗盘/比例尺 + 网格）。
  const VISUAL_TYPES = new Set([
    'title', 'subtitle', 'legend', 'categorical_legend', 'continuous_colorbar',
    'north_arrow', 'scale_bar', 'attribution', 'statistics_panel', 'chart_panel',
    'annotation', 'map_border', 'graticule',
  ]);
  const model: ExportChromeModel = {
    fromSpec: resolved.some((c) => VISUAL_TYPES.has(c.type) && c.enabled),
    panels: [],
  };
  // graticule 组件通道在任何路径下都置位（早退前 —— 终审 F4：此前
  // graticule-only spec 在此处早退，通道被自己的门饿死）
  model.graticuleEnabled = resolved.some(
    (c) => c.type === 'graticule' && c.enabled,
  );
  if (!model.fromSpec) return model;

  // ADR-0084（E-1）：槽位堆叠走共享求解器（与 live 同一实现）—— 导出
  // 此前完全没有堆叠，scale_bar 与 continuous_colorbar 同锚 bottom-right
  // 互相遮挡。floating 组件不参与（用户固定，坐标即位置）。
  // 终审 F2：fallback 注入的罗盘/比例尺（类型缺席时的注入物）也作为
  // 参与者进求解器 —— 否则注入物不占槽位，与 spec 组件同槽遮挡。
  const solverParticipants = resolved
    .filter((c) => c.enabled && !c.floating)
    .map((c) => ({
      id: c.id,
      type: c.type,
      anchor: _anchorOf(c),
      floating: false,
      origin: 'auto' as const,
    }));
  const northAbsent = !resolved.some(
    (c) => c.type === 'north_arrow' && c.enabled,
  );
  const scaleAbsent = !resolved.some(
    (c) => c.type === 'scale_bar' && c.enabled,
  );
  if (northAbsent) {
    solverParticipants.push({
      id: '__fallback_north_arrow',
      type: 'north_arrow',
      anchor: DEFAULT_COMPONENT_ANCHOR['north_arrow'],
      floating: false,
      origin: 'auto' as const,
    });
  }
  if (scaleAbsent) {
    solverParticipants.push({
      id: '__fallback_scale_bar',
      type: 'scale_bar',
      anchor: DEFAULT_COMPONENT_ANCHOR['scale_bar'],
      floating: false,
      origin: 'auto' as const,
    });
  }
  const solved = resolveComponentLayout(solverParticipants, canvas);
  const _stackOf = (c: ResolvedMapComponent) => solved.slots.get(c.id);
  const _fallbackStack = (id: string) => solved.slots.get(id);
  /** 生效锚点 = 求解器裁决槽（user 浮动碰撞侧让后可能换槽）。 */
  const _effectiveAnchor = (c: ResolvedMapComponent): ChromeAnchor =>
    (_stackOf(c)?.slot as ChromeAnchor | undefined) ?? _anchorOf(c);

  const titleComp = resolved.find((c) => c.type === 'title' && c.enabled);
  if (titleComp) {
    model.title = {
      kind: 'title',
      anchor: _effectiveAnchor(titleComp),
      rect: _floatingRectOf(titleComp, opts.viewport, canvas),
      stackIndex: _stackOf(titleComp)?.index ?? 0,
      slotSize: _stackOf(titleComp)?.slotSize ?? 0,
      text: opts.requestTitle || titleComp.text,
    };
  } else if (opts.requestTitle) {
    model.title = { kind: 'title', anchor: 'top-center', text: opts.requestTitle };
  }

  const subtitleComp = resolved.find((c) => c.type === 'subtitle' && c.enabled);
  if (subtitleComp) {
    model.subtitle = {
      kind: 'subtitle',
      anchor: _effectiveAnchor(subtitleComp),
      rect: _floatingRectOf(subtitleComp, opts.viewport, canvas),
      stackIndex: _stackOf(subtitleComp)?.index ?? 0,
      slotSize: _stackOf(subtitleComp)?.slotSize ?? 0,
      text: opts.requestSubtitle || subtitleComp.text,
    };
  } else if (opts.requestSubtitle) {
    model.subtitle = { kind: 'subtitle', anchor: 'top-center', text: opts.requestSubtitle };
  }

  const northComp = resolved.find((c) => c.type === 'north_arrow');
  if (northComp && northComp.enabled) {
    model.northArrow = {
      kind: 'north_arrow',
      anchor: _effectiveAnchor(northComp),
      rect: _floatingRectOf(northComp, opts.viewport, canvas),
      stackIndex: _stackOf(northComp)?.index ?? 0,
      slotSize: _stackOf(northComp)?.slotSize ?? 0,
    };
  } else if (!northComp) {
    // review P0：live 对缺席的 north_arrow 注入 fallback（map-spec-chrome），
    // 导出同款 —— 类型缺席不是"用户显式关闭"（那会是 enabled=false）。
    // 终审 F2：注入物带求解器槽位（与 spec 组件同槽时参与堆叠）。
    model.northArrow = {
      kind: 'north_arrow',
      anchor:
        (_fallbackStack('__fallback_north_arrow')?.slot as ChromeAnchor | undefined)
        ?? DEFAULT_COMPONENT_ANCHOR['north_arrow'],
      stackIndex: _fallbackStack('__fallback_north_arrow')?.index ?? 0,
      slotSize: _fallbackStack('__fallback_north_arrow')?.slotSize ?? 0,
    };
  }

  const scaleComp = resolved.find((c) => c.type === 'scale_bar');
  if (scaleComp && scaleComp.enabled) {
    model.scaleBar = {
      kind: 'scale_bar',
      anchor: _effectiveAnchor(scaleComp),
      rect: _floatingRectOf(scaleComp, opts.viewport, canvas),
      stackIndex: _stackOf(scaleComp)?.index ?? 0,
      slotSize: _stackOf(scaleComp)?.slotSize ?? 0,
    };
  } else if (!scaleComp) {
    model.scaleBar = {
      kind: 'scale_bar',
      anchor:
        (_fallbackStack('__fallback_scale_bar')?.slot as ChromeAnchor | undefined)
        ?? DEFAULT_COMPONENT_ANCHOR['scale_bar'],
      stackIndex: _fallbackStack('__fallback_scale_bar')?.index ?? 0,
      slotSize: _fallbackStack('__fallback_scale_bar')?.slotSize ?? 0,
    };
  }

  // 图例族：spec 组件的 layerId → legend_spec；组件 disabled → 不出图例
  // （此前导出无视 spec enabled，由 HUD 发现独裁 —— parity 修复）。
  // E-4：族内取第一个 **enabled** 成员 —— 此前 disabled 的 legend 会
  // shadow 掉 enabled 的 categorical_legend（导出丢图例）。
  const legendComp = resolved.find(
    (c) => (c.type === 'legend' || c.type === 'categorical_legend') && c.enabled,
  );
  const anyLegendFamily = resolved.some(
    (c) => c.type === 'legend' || c.type === 'categorical_legend',
  );
  if (legendComp) {
    const spec =
      (legendComp.layerId && opts.legendSpecsByLayer[legendComp.layerId]) ||
      Object.values(opts.legendSpecsByLayer).find(
        (s) => s.type === 'graduated' || s.type === 'categorical',
      ) ||
      opts.fallbackLegendSpec;
    if (spec) {
      model.legend = {
        kind: 'legend',
        anchor: _effectiveAnchor(legendComp),
        rect: _floatingRectOf(legendComp, opts.viewport, canvas),
        stackIndex: _stackOf(legendComp)?.index ?? 0,
        slotSize: _stackOf(legendComp)?.slotSize ?? 0,
        legendSpec: spec,
      };
    }
  } else if (!anyLegendFamily && opts.fallbackLegendSpec) {
    // spec 无图例组件（旧 spec）→ HUD 兜底（原行为），槽位用类型默认
    model.legend = {
      kind: 'legend',
      anchor: DEFAULT_COMPONENT_ANCHOR['legend'],
      legendSpec: opts.fallbackLegendSpec,
    };
  }

  const colorbarComp = resolved.find((c) => c.type === 'continuous_colorbar');
  if (colorbarComp && colorbarComp.enabled) {
    const spec =
      (colorbarComp.layerId && opts.legendSpecsByLayer[colorbarComp.layerId]) ||
      Object.values(opts.legendSpecsByLayer).find(
        (s) => s.type === 'continuous' || s.type === 'divergent',
      );
    if (spec) {
      model.colorbar = {
        kind: 'colorbar',
        anchor: _effectiveAnchor(colorbarComp),
        rect: _floatingRectOf(colorbarComp, opts.viewport, canvas),
        stackIndex: _stackOf(colorbarComp)?.index ?? 0,
        slotSize: _stackOf(colorbarComp)?.slotSize ?? 0,
        legendSpec: spec,
      };
    }
  }

  const attrComp = resolved.find((c) => c.type === 'attribution' && c.enabled);
  if (attrComp && attrComp.text) {
    model.attribution = {
      kind: 'attribution',
      anchor: _effectiveAnchor(attrComp),
      rect: _floatingRectOf(attrComp, opts.viewport, canvas),
      stackIndex: _stackOf(attrComp)?.index ?? 0,
      slotSize: _stackOf(attrComp)?.slotSize ?? 0,
      text: attrComp.text,
    };
  }

  // P6：图框组件（全画布，anchor 'none' —— 不参与槽位堆叠）
  const borderComp = resolved.find((c) => c.type === 'map_border' && c.enabled);
  if (borderComp) {
    model.border = {
      kind: 'map_border',
      anchor: 'none',
      variant:
        borderComp.variant ||
        (borderComp.component.variant as string | undefined) ||
        'minimal',
    };
  }

  // 终审 F1：annotation 此前在 VISUAL_TYPES 里翻转 chrome 路径却从不导出
  // （导出静默丢注释 + 压制 legacy 回退）—— 现按 live 语义导出文本注释卡。
  for (const c of resolved) {
    if (c.type !== 'annotation' || !c.enabled || !c.text.trim()) continue;
    model.panels.push({
      kind: 'annotation',
      anchor: _effectiveAnchor(c),
      rect: _floatingRectOf(c, opts.viewport, canvas),
      stackIndex: _stackOf(c)?.index ?? 0,
      slotSize: _stackOf(c)?.slotSize ?? 0,
      text: c.text,
    });
  }

  // 浮动面板族：statistics_panel / chart_panel（collapsed 面板导出为折叠
  // 标题条 —— 与 live 语义一致，不展开用户折叠的面板）。
  for (const c of resolved) {
    if (!c.enabled) continue;
    if (c.type === 'statistics_panel') {
      const stats = _parseStats(c.options['stats']);
      if (stats) {
        model.panels.push({
          kind: 'statistics',
          anchor: _effectiveAnchor(c),
          rect: _floatingRectOf(c, opts.viewport, canvas),
          stackIndex: _stackOf(c)?.index ?? 0,
          slotSize: _stackOf(c)?.slotSize ?? 0,
          stats,
          // E-2：collapsed 是 mode 无关字段（锚定面板的折叠此前在导出侧
          // 永远丢失 —— live 折叠、导出展开）。
          text: c.collapsed ? stats.title || '统计' : undefined,
        });
      }
    } else if (c.type === 'chart_panel') {
      const inline = _parseChart(c.options['chart']);
      const chartRef = c.options['chartRef'];
      let chart = inline;
      if (!chart && typeof chartRef === 'string' && chartRef && opts.loadChart) {
        try {
          chart = (await opts.loadChart(chartRef)) ?? undefined;
        } catch {
          /* 拉取失败 → 面板缺席（如实：无数据不伪造） */
        }
      }
      if (chart) {
        model.panels.push({
          kind: 'chart',
          anchor: _effectiveAnchor(c),
          rect: _floatingRectOf(c, opts.viewport, canvas),
          stackIndex: _stackOf(c)?.index ?? 0,
          slotSize: _stackOf(c)?.slotSize ?? 0,
          chart,
        });
      }
    }
  }

  return model;
}

// ── 画布绘制 ────────────────────────────────────────────────────────

interface DrawCtx {
  ctx: CanvasRenderingContext2D;
  darkMode: boolean;
  scalePx: (v: number) => number;
  targetW: number;
  targetH: number;
  style: LayoutStyle;
}

/**
 * anchor → 画布槽锚点。**y 一律是"距所属边的 margin 距离"**（top 槽距
 * 顶边、bottom 槽距底边），vAlign 标明所属边 —— 消费端按 vAlign 恰好做
 * 一次 targetH - y 换算（review P0：此前 bottom 槽返回画布坐标又被二次
 * 相减，全部底部组件被画到顶部）。x 直接是画布坐标（左/中/右对齐基线）。
 */
export function anchorOrigin(
  anchor: ChromeAnchor,
  d: { targetW: number; targetH: number; marginX: number; marginY: number },
): { x: number; y: number; align: 'left' | 'center' | 'right'; vAlign: 'top' | 'bottom' } {
  switch (anchor) {
    case 'top-left':
      return { x: d.marginX, y: d.marginY, align: 'left', vAlign: 'top' };
    case 'top-center':
      return { x: d.targetW / 2, y: d.marginY, align: 'center', vAlign: 'top' };
    case 'top-right':
      return { x: d.targetW - d.marginX, y: d.marginY, align: 'right', vAlign: 'top' };
    case 'bottom-left':
      return { x: d.marginX, y: d.marginY, align: 'left', vAlign: 'bottom' };
    case 'bottom-center':
      return { x: d.targetW / 2, y: d.marginY, align: 'center', vAlign: 'bottom' };
    case 'bottom-right':
      return { x: d.targetW - d.marginX, y: d.marginY, align: 'right', vAlign: 'bottom' };
    default:
      return { x: d.marginX, y: d.marginY, align: 'left', vAlign: 'top' };
  }
}

function _text(d: DrawCtx, s: string, x: number, y: number, align: CanvasTextAlign) {
  d.ctx.textAlign = align;
  d.ctx.fillText(s, x, y);
  d.ctx.textAlign = 'left';
}

/** title/subtitle（anchor 对齐；vAlign=bottom 时 y 为基线底部）。 */
export function drawChromeText(
  d: DrawCtx,
  el: ExportChromeElement,
  fontPx: number,
  color: string,
  opts: { marginX: number; marginY?: number; dy?: number },
) {
  if (!el.text) return;
  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 52 });
  d.ctx.fillStyle = color;
  d.ctx.font = `bold ${d.scalePx(fontPx)}px ${d.style.fontFamily}`;
  // margin 语义统一：origin.y 已是画布像素（调用方传入 scalePx 后的值）；
  // bottom 槽恰好一次 targetH - y 换算。
  const y = origin.vAlign === 'bottom' ? d.targetH - origin.y : origin.y + (opts.dy ?? 0);
  _text(d, el.text, origin.x, y, origin.align);
}

/** 罗盘 —— 与 live 同一旋转约定（-bearing 逆时针；此前导出符号相反）。 */
export function drawChromeNorthArrow(
  d: DrawCtx,
  el: ExportChromeElement,
  bearing: number,
  opts: { marginX: number; marginY?: number },
) {
  const r = d.scalePx(28);
  const origin = el.rect
    ? { x: el.rect.x + r, y: el.rect.y + r }
    : (() => {
        const o = anchorOrigin(el.anchor, {
          targetW: d.targetW, targetH: d.targetH,
          marginX: opts.marginX, marginY: (opts.marginY ?? 64),
        });
        return {
          x: o.align === 'right' ? o.x - r : o.align === 'center' ? o.x : o.x + r,
          y: o.vAlign === 'bottom' ? d.targetH - o.y - r : o.y + r,
        };
      })();
  const { ctx } = d;
  ctx.save();
  ctx.translate(origin.x, origin.y);
  // parity 修复：live 是 rotate(-bearing)（CSS 逆时针）；canvas y 轴向下，
  // 正角为顺时针 —— 取 -bearing 才与 live 同向。
  ctx.rotate((-bearing * Math.PI) / 180);

  ctx.shadowColor = 'rgba(0,0,0,0.4)';
  ctx.shadowBlur = d.scalePx(6);
  ctx.beginPath();
  ctx.moveTo(0, -r);
  ctx.lineTo(r * 0.35, 0);
  ctx.lineTo(0, r * 0.2);
  ctx.lineTo(-r * 0.35, 0);
  ctx.closePath();
  ctx.fillStyle = d.style.accentColor || '#e53e3e';
  ctx.fill();

  ctx.beginPath();
  ctx.moveTo(0, r);
  ctx.lineTo(r * 0.35, 0);
  ctx.lineTo(0, r * 0.2);
  ctx.lineTo(-r * 0.35, 0);
  ctx.closePath();
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#f8fafc';
  ctx.fill();

  ctx.shadowBlur = 0;
  ctx.beginPath();
  ctx.arc(0, 0, d.scalePx(4), 0, 2 * Math.PI);
  ctx.fillStyle = '#1e293b';
  ctx.fill();
  ctx.restore();

  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.95)' : '#1e293b';
  ctx.font = `bold ${d.scalePx(13)}px ${d.style.fontFamily}`;
  _text(d, 'N', origin.x, origin.y - r - d.scalePx(6), 'center');
}

/** 比例尺（anchor 槽位；nice-number 算法与 legacy 同源）。 */
export function drawChromeScaleBar(
  d: DrawCtx,
  el: ExportChromeElement,
  metersPerPx: number,
  pxPerLogical: number,
  opts: { marginX: number; marginY?: number },
) {
  const { ctx } = d;
  const logicalW = d.targetW / pxPerLogical;
  const targetPx = Math.round(logicalW * 0.12);
  // ADR-0084（E-3）：与 live 共用同一 nice-number 算法（scale-math.ts）。
  const { meters: nice, px: barPxLogical } = computeNiceScale(metersPerPx, targetPx);
  const barPx = barPxLogical * pxPerLogical;
  const barLabel = formatScaleLabel(nice);
  const barH = d.scalePx(8);

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 52 });
  const bx = origin.align === 'right' ? origin.x - barPx : origin.align === 'center' ? origin.x - barPx / 2 : origin.x;
  const by = origin.vAlign === 'bottom' ? d.targetH - origin.y - barH : origin.y;

  ctx.strokeStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.8)';
  ctx.lineWidth = d.scalePx(1.5);
  ctx.strokeRect(bx, by, barPx, barH);
  const segCount = 4;
  const segW = barPx / segCount;
  for (let i = 0; i < segCount; i++) {
    ctx.fillStyle =
      i % 2 === 0
        ? d.darkMode ? 'rgba(255,255,255,0.9)' : 'rgba(0,0,0,0.8)'
        : 'rgba(0,0,0,0)';
    ctx.fillRect(bx + i * segW, by, segW, barH);
  }
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.95)' : '#1e293b';
  ctx.font = `bold ${d.scalePx(13)}px ${d.style.fontFamily}`;
  _text(d, '0', bx, by - d.scalePx(4), 'left');
  _text(d, barLabel, bx + barPx, by - d.scalePx(4), 'right');
}

/** 连续色条 —— 渐变 ramp + min/mid/max + unit（与 live colorbar 同形态）。 */
export function drawChromeColorbar(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  const spec = el.legendSpec as
    | { min?: number; max?: number; palette_colors?: string[]; unit?: string; field?: string }
    | undefined;
  // E-5：与 live 同款退化语义 —— 无 palette 不绘制（不伪造默认 ramp）；
  // 缺 min/max 只画裸条不带数值标签（live colorbar.tsx 同款），不再整体丢弃。
  const rawColors = spec?.palette_colors ?? [];
  if (rawColors.length === 0 || !spec) return;
  const colors =
    rawColors.length >= 2 ? rawColors : [rawColors[0], rawColors[0]];
  const hasRange =
    typeof spec.min === 'number' &&
    typeof spec.max === 'number' &&
    spec.min !== spec.max;
  const { ctx } = d;
  const padding = d.scalePx(10);
  const barW = d.scalePx(160);
  const barH = d.scalePx(10);
  const titleH = spec.field ? d.scalePx(18) : 0;
  // 终审 F6：无量化范围 = 裸条 —— 不预留数值标签带（此前空占 ~16px）
  const labelsH = hasRange ? d.scalePx(16) : 0;
  const boxW = padding * 2 + barW;
  const boxH = padding * 2 + titleH + barH + labelsH;

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 56 });
  const lx = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
  const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;

  _chromePanel(d, lx, ly, boxW, boxH);
  let y = ly + padding;
  if (spec.field) {
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.7)' : 'rgba(100,116,139,0.9)';
    ctx.font = `${d.scalePx(10)}px monospace`;
    _text(d, spec.field.toUpperCase(), lx + padding, y + d.scalePx(10), 'left');
    y += titleH;
  }
  const grad = ctx.createLinearGradient(lx + padding, 0, lx + padding + barW, 0);
  colors.forEach((c, i) => grad.addColorStop(colors.length === 1 ? 1 : i / (colors.length - 1), c));
  ctx.fillStyle = grad;
  ctx.fillRect(lx + padding, y, barW, barH);
  ctx.strokeStyle = 'rgba(128,128,128,0.4)';
  ctx.lineWidth = d.scalePx(0.5);
  ctx.strokeRect(lx + padding, y, barW, barH);

  const fmt = (n: number) =>
    n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : n.toFixed(1);
  const suffix = spec.unit ? ` ${spec.unit}` : '';
  y += barH + d.scalePx(4);
  if (hasRange) {
    // 数值标签只在有量化范围时绘制（live 同款：无范围 = 裸条）
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.6)' : 'rgba(100,116,139,0.8)';
    ctx.font = `${d.scalePx(10)}px sans-serif`;
    _text(d, `${fmt(spec.min!)}${suffix}`, lx + padding, y + d.scalePx(10), 'left');
    _text(
      d,
      `${fmt((spec.min! + spec.max!) / 2)}`,
      lx + padding + barW / 2,
      y + d.scalePx(10),
      'center',
    );
    _text(d, `${fmt(spec.max!)}${suffix}`, lx + padding + barW, y + d.scalePx(10), 'right');
  }
}

/** 离散/分级图例（anchor 槽位版 _drawDiscreteLegend）。 */
export function drawChromeLegend(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  const spec = el.legendSpec as
    | {
        type: string;
        field?: string;
        breaks?: number[];
        palette_colors?: string[];
        palette?: string;
        categories?: Array<{ color?: string; label?: string; key?: string }>;
        min?: number;
        max?: number;
        unit?: string;
      }
    | undefined;
  if (!spec) return;

  let colors: string[] = [];
  let labels: string[] = [];
  const fmt = (n: number) =>
    n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : n.toFixed(1);
  if (spec.type === 'categorical') {
    colors = (spec.categories || []).map((c) => c.color || '#888');
    labels = (spec.categories || []).map((c) => c.label || c.key || '');
  } else {
    colors = spec.palette_colors || [];
    if (spec.breaks && spec.breaks.length >= 2) {
      for (let i = 0; i < spec.breaks.length - 1; i++) {
        labels.push(`${fmt(spec.breaks[i])} – ${fmt(spec.breaks[i + 1])}`);
      }
    } else if (typeof spec.min === 'number' && typeof spec.max === 'number') {
      labels = [fmt(spec.min), fmt((spec.min + spec.max) / 2), fmt(spec.max)];
      while (labels.length < colors.length) labels.push('');
    }
  }
  const classes = Math.min(colors.length, labels.length);
  if (classes === 0) return;

  const { ctx } = d;
  const itemH = d.scalePx(22);
  const itemW = d.scalePx(18);
  const padding = d.scalePx(10);
  const gapX = d.scalePx(8);
  ctx.font = `${d.scalePx(11)}px sans-serif`;
  let maxTextW = 0;
  for (const label of labels) maxTextW = Math.max(maxTextW, ctx.measureText(label).width);
  const legendW = padding * 2 + itemW + gapX + maxTextW + d.scalePx(10);
  const legendH = padding * 2 + d.scalePx(24) + classes * itemH;

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 56 });
  const lx = origin.align === 'right' ? origin.x - legendW : origin.align === 'center' ? origin.x - legendW / 2 : origin.x;
  const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - legendH : origin.y;

  _chromePanel(d, lx, ly, legendW, legendH);
  ctx.fillStyle = d.darkMode ? '#00f2ff' : '#1e293b';
  ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
  _text(d, `字段: ${spec.field || '未知字段'}`, lx + padding, ly + padding + d.scalePx(12), 'left');
  for (let i = 0; i < classes; i++) {
    const iy = ly + padding + d.scalePx(24) + i * itemH;
    ctx.fillStyle = colors[i];
    ctx.fillRect(lx + padding, iy, itemW, itemH - d.scalePx(4));
    ctx.strokeStyle = 'rgba(128,128,128,0.4)';
    ctx.lineWidth = d.scalePx(0.5);
    ctx.strokeRect(lx + padding, iy, itemW, itemH - d.scalePx(4));
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.85)' : '#334155';
    ctx.font = `${d.scalePx(11)}px sans-serif`;
    _text(d, labels[i], lx + padding + itemW + gapX, iy + itemH - d.scalePx(8), 'left');
  }
}

/** 署名行（anchor 槽位；此前导出完全不读 spec attribution 组件）。 */
export function drawChromeAttribution(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  if (!el.text) return;
  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 22 });
  const y = origin.vAlign === 'bottom' ? d.targetH - origin.y : origin.y;
  d.ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.45)' : 'rgba(0,0,0,0.4)';
  d.ctx.font = `${d.scalePx(11)}px sans-serif`;
  _text(d, el.text, origin.x, y, origin.align);
}

/** 统计卡（title + label/value 行）。 */
export function drawChromeStatsPanel(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  if (!el.stats) return;
  const { ctx } = d;
  const padding = d.scalePx(12);
  const rowH = d.scalePx(22);
  const titleH = el.stats.title ? d.scalePx(26) : 0;
  const collapsed = el.text !== undefined;
  const boxW = el.rect?.width ?? d.scalePx(240);
  const boxH = collapsed
    ? d.scalePx(36)
    : padding * 2 + titleH + el.stats.items.length * rowH;

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 90 });
  const lx = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
  const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;

  _chromePanel(d, lx, ly, boxW, boxH);
  let y = ly + padding;
  if (el.stats.title || collapsed) {
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#1e293b';
    ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
    _text(d, collapsed ? el.text || el.stats.title || '统计' : el.stats.title!, lx + padding, y + d.scalePx(12), 'left');
    y += titleH;
  }
  if (collapsed) return;
  for (const item of el.stats.items) {
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.6)' : 'rgba(100,116,139,0.9)';
    ctx.font = `${d.scalePx(11)}px sans-serif`;
    _text(d, item.label ?? '', lx + padding, y + d.scalePx(14), 'left');
    ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.95)' : '#0f172a';
    ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
    const value = `${item.value ?? ''}${item.unit ? ` ${item.unit}` : ''}`;
    _text(d, value, lx + boxW - padding, y + d.scalePx(14), 'right');
    y += rowH;
  }
}

/** 静态图表（bar/line/pie/scatter 的确定性 canvas 绘制）。 */
export function drawChromeChartPanel(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
) {
  if (!el.chart || el.chart.data.length === 0) return;
  const { ctx } = d;
  const chart = el.chart;
  const padding = d.scalePx(12);
  const titleH = d.scalePx(24);
  const axisH = chart.type === 'pie' ? 0 : d.scalePx(26);
  const boxW = el.rect?.width ?? d.scalePx(300);
  const boxH = el.rect?.height ?? d.scalePx(220);

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 90 });
  const lx = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
  const ly = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;

  _chromePanel(d, lx, ly, boxW, boxH);
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.9)' : '#1e293b';
  ctx.font = `bold ${d.scalePx(12)}px sans-serif`;
  _text(d, chart.title, lx + padding, ly + padding + d.scalePx(12), 'left');

  const plotX = lx + padding;
  const plotY = ly + padding + titleH;
  const plotW = boxW - padding * 2;
  const plotH = boxH - padding * 2 - titleH - axisH;
  if (plotW <= 0 || plotH <= 0) return;

  const accent = d.style.accentColor || '#3182bd';
  const gridColor = d.darkMode ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.08)';
  const labelColor = d.darkMode ? 'rgba(255,255,255,0.6)' : 'rgba(100,116,139,0.9)';
  const CHART_COLORS = ['#3182bd', '#e6550d', '#31a354', '#756bb1', '#e41a1c', '#ffd92f'];

  if (chart.type === 'pie') {
    const total = chart.data.reduce((s, p) => s + (p.value ?? 0), 0);
    if (total <= 0) return;
    const cx = plotX + plotW / 2;
    const cy = plotY + plotH / 2;
    const r = Math.min(plotW, plotH) / 2;
    let angle = -Math.PI / 2;
    chart.data.forEach((p, i) => {
      const frac = (p.value ?? 0) / total;
      if (frac <= 0) return;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, angle, angle + frac * 2 * Math.PI);
      ctx.closePath();
      ctx.fillStyle = CHART_COLORS[i % CHART_COLORS.length];
      ctx.fill();
      angle += frac * 2 * Math.PI;
    });
    return;
  }

  if (chart.type === 'scatter') {
    const xs = chart.data.map((p) => p.x ?? 0);
    const ys = chart.data.map((p) => p.y ?? 0);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const spanX = maxX - minX || 1;
    const spanY = maxY - minY || 1;
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = d.scalePx(0.5);
    ctx.strokeRect(plotX, plotY, plotW, plotH);
    ctx.fillStyle = accent;
    for (const p of chart.data) {
      const px = plotX + (((p.x ?? 0) - minX) / spanX) * plotW;
      const py = plotY + plotH - (((p.y ?? 0) - minY) / spanY) * plotH;
      ctx.beginPath();
      ctx.arc(px, py, d.scalePx(3), 0, 2 * Math.PI);
      ctx.fill();
    }
  } else {
    const values = chart.data.map((p) => p.value ?? 0);
    const maxV = Math.max(...values, 0);
    const minV = Math.min(...values, 0);
    const span = maxV - minV || 1;
    // 网格
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = d.scalePx(0.5);
    for (let g = 0; g <= 3; g++) {
      const gy = plotY + (g / 3) * plotH;
      ctx.beginPath();
      ctx.moveTo(plotX, gy);
      ctx.lineTo(plotX + plotW, gy);
      ctx.stroke();
    }
    const n = chart.data.length;
    if (chart.type === 'bar') {
      const slotW = plotW / Math.max(n, 1);
      const barW = slotW * 0.6;
      chart.data.forEach((p, i) => {
        const v = p.value ?? 0;
        const h = ((v - minV) / span) * plotH;
        const bx = plotX + i * slotW + (slotW - barW) / 2;
        const by = plotY + plotH - h;
        ctx.fillStyle = CHART_COLORS[i % CHART_COLORS.length];
        ctx.fillRect(bx, by, barW, h);
      });
    } else {
      // line
      ctx.strokeStyle = accent;
      ctx.lineWidth = d.scalePx(2);
      ctx.beginPath();
      chart.data.forEach((p, i) => {
        const px = plotX + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
        const py = plotY + plotH - (((p.value ?? 0) - minV) / span) * plotH;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
    }
    // x 轴标签（首/中/尾，避免重叠）
    ctx.fillStyle = labelColor;
    ctx.font = `${d.scalePx(10)}px sans-serif`;
    const labelAt = (i: number) => chart.data[i]?.name ?? '';
    if (n > 0) _text(d, labelAt(0), plotX, plotY + plotH + d.scalePx(14), 'left');
    if (n > 2) _text(d, labelAt(Math.floor(n / 2)), plotX + plotW / 2, plotY + plotH + d.scalePx(14), 'center');
    if (n > 1) _text(d, labelAt(n - 1), plotX + plotW, plotY + plotH + d.scalePx(14), 'right');
  }
  // 轴标签
  if (chart.x_label || chart.y_label) {
    ctx.fillStyle = labelColor;
    ctx.font = `${d.scalePx(10)}px sans-serif`;
    if (chart.y_label) _text(d, chart.y_label, plotX, plotY - d.scalePx(4), 'left');
    if (chart.x_label) _text(d, chart.x_label, plotX + plotW, plotY + plotH + d.scalePx(26), 'right');
  }
}

/** chrome 面板底色（圆角半透明卡）。 */
function _chromePanel(d: DrawCtx, x: number, y: number, w: number, h: number) {
  const { ctx } = d;
  ctx.fillStyle = d.darkMode ? 'rgba(0,10,20,0.82)' : 'rgba(255,255,255,0.88)';
  ctx.beginPath();
  const rad = d.scalePx(8);
  ctx.moveTo(x + rad, y);
  ctx.lineTo(x + w - rad, y);
  ctx.arcTo(x + w, y, x + w, y + rad, rad);
  ctx.lineTo(x + w, y + h - rad);
  ctx.arcTo(x + w, y + h, x + w - rad, y + h, rad);
  ctx.lineTo(x + rad, y + h);
  ctx.arcTo(x, y + h, x, y + h - rad, rad);
  ctx.lineTo(x, y + rad);
  ctx.arcTo(x, y, x + rad, y, rad);
  ctx.closePath();
  ctx.fill();
}

/** Map Border —— 全画布图框（P6：与 live map-border.tsx 三变体同语义）。 */
export function drawChromeMapBorder(
  d: DrawCtx,
  el: ExportChromeElement,
): void {
  const { ctx } = d;
  const variant = el.variant || 'minimal';
  const ink = d.darkMode ? 'rgba(255,255,255,0.85)' : 'rgba(30,41,59,0.9)';
  ctx.strokeStyle = ink;

  const inset = variant === 'minimal' ? d.scalePx(8) : d.scalePx(10);
  const w = d.targetW - inset * 2;
  const h = d.targetH - inset * 2;

  if (variant === 'report') {
    ctx.lineWidth = d.scalePx(3);
    ctx.strokeRect(inset, inset, w, h);
    ctx.lineWidth = d.scalePx(1);
    const inset2 = inset + d.scalePx(5);
    ctx.strokeRect(inset2, inset2, d.targetW - inset2 * 2, d.targetH - inset2 * 2);
    return;
  }
  if (variant === 'academic') {
    // 外框
    ctx.lineWidth = d.scalePx(2);
    ctx.strokeRect(inset, inset, w, h);
    // 内框
    ctx.lineWidth = d.scalePx(1);
    const inset2 = inset + d.scalePx(4);
    ctx.strokeRect(inset2, inset2, d.targetW - inset2 * 2, d.targetH - inset2 * 2);
    // 四角刻度（与 live 四角 tick 同位：外框角向内 12px）
    const tick = d.scalePx(12);
    ctx.lineWidth = d.scalePx(3);
    ctx.beginPath();
    // 左上/右上/左下/右下：横竖两段
    ctx.moveTo(inset, inset + tick); ctx.lineTo(inset, inset);
    ctx.lineTo(inset + tick, inset);
    ctx.moveTo(d.targetW - inset - tick, inset); ctx.lineTo(d.targetW - inset, inset);
    ctx.lineTo(d.targetW - inset, inset + tick);
    ctx.moveTo(inset, d.targetH - inset - tick); ctx.lineTo(inset, d.targetH - inset);
    ctx.lineTo(inset + tick, d.targetH - inset);
    ctx.moveTo(d.targetW - inset - tick, d.targetH - inset);
    ctx.lineTo(d.targetW - inset, d.targetH - inset);
    ctx.lineTo(d.targetW - inset, d.targetH - inset - tick);
    ctx.stroke();
    return;
  }
  // minimal
  ctx.lineWidth = d.scalePx(1.5);
  ctx.strokeRect(inset, inset, w, h);
}

/** Annotation —— 文本注释卡（终审 F1：与 live annotation.tsx 同语义：左边
 * 线强调 + 弱文本；多行按 \n 分行绘制）。 */
export function drawChromeAnnotation(
  d: DrawCtx,
  el: ExportChromeElement,
  opts: { marginX: number; marginY?: number },
): void {
  if (!el.text) return;
  const { ctx } = d;
  const lines = el.text.split('\n').slice(0, 8); // 有界：注释卡 ≤8 行
  const padding = d.scalePx(10);
  const lineH = d.scalePx(16);
  const boxW = Math.min(d.scalePx(360), d.targetW * 0.5);
  const boxH = padding * 2 + lineH * lines.length;

  const origin = el.rect
    ? { x: el.rect.x, y: el.rect.y, align: 'left' as const, vAlign: 'top' as const }
    : anchorOrigin(el.anchor, { targetW: d.targetW, targetH: d.targetH, marginX: opts.marginX, marginY: opts.marginY ?? 90 });
  const x = origin.align === 'right' ? origin.x - boxW : origin.align === 'center' ? origin.x - boxW / 2 : origin.x;
  const y = origin.vAlign === 'bottom' ? d.targetH - origin.y - boxH : origin.y;

  _chromePanel(d, x, y, boxW, boxH);
  // 左边线强调（live border-l-2 同语义）
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.85)' : 'rgba(30,41,59,0.85)';
  ctx.fillRect(x, y, d.scalePx(2), boxH);
  ctx.fillStyle = d.darkMode ? 'rgba(255,255,255,0.65)' : 'rgba(100,116,139,0.95)';
  ctx.font = `${d.scalePx(12)}px ${d.style.fontFamily}`;
  lines.forEach((line, i) => {
    _text(d, line.slice(0, 80), x + padding + d.scalePx(2), y + padding + lineH * (i + 0.75), 'left');
  });
}
