/**
 * StyleIntent —— typed 样式意图词表（Workbench V4 / Wave 7）。
 *
 * 「颜色浅一点 / 改成蓝色渐变 / 分成 7 级 / 边界线细一些」这类语义请求
 * 不再翻译成任意 CSS/MapLibre paint JSON —— Agent 与用户 UI 共用这个
 * 封闭词表，applier 把意图投影到 LayerStyle（本地 runtime）+
 * MapSpec paint（spec 承载层走 patch_layer_style 持久通道）。
 *
 * 纪律：
 * - 词表封闭（StyleIntentKind），参数逐字段校验/钳制 —— 不是样式引擎；
 * - 语义修饰（lighten/darken/thinner/thicker）是**相对**意图，applier 按
 *   当前样式求值成绝对值，色彩运算用 HSL（感知均匀），结果钳制在 sRGB 域；
 * - 不理解/越界的意图如实报错（返回 null），绝不「猜一个最接近的」。
 */
import type { LayerStyle } from '@/lib/types/layer';
import { useHudStore } from '@/lib/store/useHudStore';
import { commitLayerStyleAndCommit } from '@/lib/mapspec/user-mutation';

export type StyleIntentKind =
  | 'set_color'            // { color: '#rrggbb' }
  | 'set_palette'          // { palette: catalog palette id }
  | 'lighten'              // { amount?: 0..1 }  默认 0.15
  | 'darken'               // { amount?: 0..1 }
  | 'set_opacity'          // { opacity: 0..1 }
  | 'set_stroke_width'     // { width: px > 0 }
  | 'thinner'              // { amount?: 0.1..0.9 } 默认 0.25（按比例变细）
  | 'thicker'              // { amount?: 0.1..0.9 }
  | 'set_classification'   // { method, classes: 2..9 }（分类渲染的级数）
  | 'set_point_size';      // { size: px > 0 }

export interface StyleIntent {
  kind: StyleIntentKind;
  color?: string;
  palette?: string;
  opacity?: number;
  width?: number;
  size?: number;
  classes?: number;
  method?: 'quantiles' | 'equal_interval' | 'natural_breaks' | 'std_dev' | 'head_tail';
  amount?: number;
}

/** 目录 palette 词表（component-catalog.generated.json 的镜像子集；分级用）。 */
export const STYLE_PALETTES: ReadonlySet<string> = new Set([
  'Blues', 'Greens', 'Oranges', 'Purples', 'Reds', 'Gray',
  'Viridis', 'Inferno', 'Magma', 'Plasma', 'RdYlGn', 'RdBu', 'PuOr',
  'YlOrRd', 'Set1', 'Set2', 'Dark2', 'Pastel1',
]);

export const CLASSIFICATION_METHODS: ReadonlySet<string> = new Set([
  'quantiles', 'equal_interval', 'natural_breaks', 'std_dev', 'head_tail',
]);

const HEX_RE = /^#[0-9a-fA-F]{6}$/;

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/** #rrggbb → [h(0..360), s(0..1), l(0..1)]。 */
export function hexToHsl(hex: string): [number, number, number] {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0));
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return [h * 60, s, l];
}

function hslToHex(h: number, s: number, l: number): string {
  const f = (n: number): string => {
    const k = (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const v = l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    return Math.round(clamp(v, 0, 1) * 255).toString(16).padStart(2, '0');
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

/** 亮度偏移（正=变浅，负=变深；±amount 进 HSL L 通道，钳制 sRGB 域）。 */
export function shiftLightness(hex: string, amount: number): string {
  const [h, s, l] = hexToHsl(hex);
  return hslToHex(h, s, clamp(l + amount, 0.05, 0.95));
}

/**
 * 求值：把意图投影到**完整的新 LayerStyle**（不可变 —— 复制当前样式后改）。
 * 非法/不适用意图返回 null（调用方给用户明确回执，不静默）。
 */
export function evaluateStyleIntent(current: LayerStyle, intent: StyleIntent): LayerStyle | null {
  const next: LayerStyle = { ...current };
  switch (intent.kind) {
    case 'set_color':
      if (!intent.color || !HEX_RE.test(intent.color)) return null;
      next.color = intent.color;
      return next;
    case 'set_palette':
      // Review R1（architecture CRITICAL）：palette 字段当前在渲染器/
      // compile/图例派生中零消费 —— 落库即是静默无效操作。诚实失败，
      // 等后端 resolver 接管 palette 语义后再放开（词表保留作合约预埋）。
      return null;
    case 'lighten':
    case 'darken': {
      const amount = clamp(intent.amount ?? 0.15, 0, 0.9);
      const base = typeof current.color === 'string' && HEX_RE.test(current.color)
        ? current.color
        : '#15803d';
      next.color = shiftLightness(base, intent.kind === 'lighten' ? amount : -amount);
      return next;
    }
    case 'set_opacity':
      if (typeof intent.opacity !== 'number' || !Number.isFinite(intent.opacity)) return null;
      next.opacity = clamp(Math.round(intent.opacity * 100) / 100, 0, 1);
      return next;
    case 'set_stroke_width':
      if (typeof intent.width !== 'number' || !(intent.width > 0)) return null;
      next.strokeWidth = clamp(Math.round(intent.width * 10) / 10, 0.2, 24);
      return next;
    case 'thinner':
    case 'thicker': {
      const factor = intent.kind === 'thinner' ? (1 - clamp(intent.amount ?? 0.25, 0.05, 0.9)) : (1 + clamp(intent.amount ?? 0.25, 0.05, 3));
      const base = typeof current.strokeWidth === 'number' && current.strokeWidth > 0 ? current.strokeWidth : 1.5;
      next.strokeWidth = clamp(Math.round(base * factor * 10) / 10, 0.2, 24);
      return next;
    }
    case 'set_classification':
      // Review R1（architecture CRITICAL）：分级只在后端建图时计算
      // （app/tools/cartography.py）；前端改 classification 无渲染消费面
      // 且会以 user 语义清认证（假「待同步」）。诚实失败直至后端提供
      // reclassify 通道。
      return null;
    case 'set_point_size':
      if (typeof intent.size !== 'number' || !(intent.size > 0)) return null;
      next.pointSize = clamp(Math.round(intent.size * 10) / 10, 1, 40);
      return next;
    default:
      return null;
  }
}

/** LayerStyle → MapSpec paint 投影（与 layer-ops 同款字段族）。 */
function styleToPaint(style: LayerStyle): Record<string, unknown> {
  const paint: Record<string, unknown> = {};
  if (typeof style.color === 'string') paint.color = style.color;
  if (typeof style.strokeColor === 'string') paint.strokeColor = style.strokeColor;
  if (typeof style.strokeWidth === 'number') {
    paint.strokeWidth = style.strokeWidth;
    paint.width = style.strokeWidth;
  }
  if (typeof style.opacity === 'number') paint.opacity = style.opacity;
  if (typeof style.pointSize === 'number') paint.radius = style.pointSize;
  return paint;
}

/**
 * 应用意图（唯一收口）：乐观 store 更新 + spec 承载层走 patch_layer_style
 * 持久通道（与手动样式面板同一条 CAS 串行链）。返回 null = 意图非法。
 */
export async function applyStyleIntent(layerId: string, intent: StyleIntent): Promise<'applied' | 'invalid' | 'locked' | 'thematic_protected'> {
  const layer = useHudStore.getState().layers.find((l) => l.id === layerId);
  if (!layer) return 'invalid';
  if (useHudStore.getState().lockedLayerIds.includes(layerId)) return 'locked';
  // Review R1（GIS F3 MAJOR）：分级/连续专题层的颜色来自 legend_spec 色带
  // （step/interpolate 表达式）—— 设 flat color 会静默抹平分级编码（图例
  // 与地图分叉）。此类层拒绝绝对/相对色彩意图：如示失败，不是「猜一个」。
  const thematic = layer.legend_spec?.type != null
    && layer.legend_spec.type !== 'categorical';
  if (thematic && (intent.kind === 'set_color' || intent.kind === 'lighten' || intent.kind === 'darken')) {
    return 'thematic_protected';
  }
  const next = evaluateStyleIntent(layer.style ?? {}, intent);
  if (!next) return 'invalid';
  useHudStore.getState().updateLayer(layerId, { style: next });
  if (layer._mapspecLayerId) {
    try {
      await commitLayerStyleAndCommit(layerId, styleToPaint(next));
    } catch {
      // 提交失败：user-mutation 通道已回滚 + toast；乐观态随之收敛。
    }
  }
  return 'applied';
}
