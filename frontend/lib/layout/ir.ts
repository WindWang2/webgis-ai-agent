/**
 * C2 共享版面描述 IR（LayoutIR v2）—— TS 镜像面（V11 W0.1，ADR-0160）。
 *
 * Python 权威实现：``app/lib/cartography/layout_description.py``
 * （``buildLayoutIr`` / ``validateLayoutIr``，LAYOUT_IR_VERSION = 2）。
 * 本模块逐字段镜像其装配与校验语义：三套整饰渲染器（React DOM /
 * canvas / SVG）从**同一 IR** 渲染等价结果 —— 契约冻结后各渲染器不得
 * 自行裁决（G3 的解药）。
 *
 * 双端 parity 由 golden corpus 锁定（tests/cartography/golden_corpus/
 * layout_ir/：pytest 与 vitest 消费同一批 fixture，逐字段对拍）。
 * 确定性契约：纯函数、无随机、无 locale，同输入恒同输出。
 */

export const LAYOUT_IR_VERSION = 2;

/** 组件种类枚举（与 component_registry 类型词汇对齐）。 */
export const LAYOUT_IR_COMPONENT_KINDS = [
  'north_arrow', 'scale_bar', 'legend', 'title', 'subtitle',
  'author', 'data_source', 'attribution', 'chart_panel', 'inset_map',
  'graticule', 'text_note',
] as const;

/** 组件角色（决定层级默认值与遮挡裁决优先级；W4/W5 优先级模型共享）。 */
export const LAYOUT_IR_ROLES = ['primary', 'secondary', 'decorative'] as const;

/** 锚点九宫格词汇（frame 表达方式；与 compose.ts 的 anchor 语义对齐）。 */
export const LAYOUT_IR_ANCHORS = [
  'top_left', 'top_center', 'top_right',
  'middle_left', 'middle_center', 'middle_right',
  'bottom_left', 'bottom_center', 'bottom_right',
] as const;

/** 文本断行模式（W4 多语言断行契约的前置词汇：CJK 按字、拉丁按词）。 */
export const LAYOUT_IR_WRAP_MODES = ['none', 'cjk_char', 'latin_word', 'auto'] as const;

export type LayoutIrComponentKind = (typeof LAYOUT_IR_COMPONENT_KINDS)[number];
export type LayoutIrRole = (typeof LAYOUT_IR_ROLES)[number];
export type LayoutIrAnchor = (typeof LAYOUT_IR_ANCHORS)[number];
export type LayoutIrWrapMode = (typeof LAYOUT_IR_WRAP_MODES)[number];

export interface LayoutIrFrame {
  x: number;
  y: number;
  width: number;
  height: number;
  anchor: LayoutIrAnchor;
  z: number;
}

export interface LayoutIrConstraints {
  minWidth?: number;
  maxWidth?: number;
  minHeight?: number;
  maxHeight?: number;
  aspectLocked?: boolean;
  marginPx?: number;
}

/** 样式 token 引用（token = style_tokens 词汇；渲染器解析为具体视觉量）。 */
export interface LayoutIrStyle {
  token: string | null;
  opacity?: number;
  borderRadiusPx?: number;
  shadow?: boolean;
}

export interface LayoutIrTypography {
  fontFamily: string;
  fontSizePx: number;
  fontWeight: number;
  lineHeightPx: number;
  align: 'left' | 'center' | 'right';
  wrapMode: LayoutIrWrapMode;
}

export interface LayoutIrComponent {
  id: string;
  kind: LayoutIrComponentKind;
  role: LayoutIrRole;
  frame: LayoutIrFrame;
  constraints: LayoutIrConstraints;
  style: LayoutIrStyle;
  typography: LayoutIrTypography | null;
}

export interface LayoutIrDegradation {
  code: string;
  detail: string;
}

export interface LayoutIr {
  version: 2;
  canvas: { widthPx: number; heightPx: number };
  /** z 升序（bottom→top）；渲染器按序绘制即可获得一致层级。 */
  layers: Array<{ id: string; z: number }>;
  components: LayoutIrComponent[];
  degradations: LayoutIrDegradation[];
}

/** 装配入参（渲染前决策面，来自 compose / 组件裁决）。 */
export interface LayoutIrComponentInput {
  id: string;
  kind: LayoutIrComponentKind;
  role?: LayoutIrRole;
  frame: Partial<LayoutIrFrame> & { anchor?: LayoutIrAnchor; z?: number };
  constraints?: LayoutIrConstraints;
  style?: Partial<LayoutIrStyle>;
  typography?: LayoutIrTypography | null;
}

/**
 * 装配 C2 IR（与 Python build_layout_ir 同语义）：规范化（补默认值）+
 * z 升序稳定排序（tie-break id 字典序），不裁剪、不重排语义。
 */
export function buildLayoutIr(params: {
  canvas: { widthPx: number; heightPx: number };
  components: LayoutIrComponentInput[];
  degradations?: LayoutIrDegradation[];
}): LayoutIr {
  const normalized: LayoutIrComponent[] = params.components.map((comp) => ({
    id: comp.id,
    kind: comp.kind,
    role: comp.role ?? 'decorative',
    frame: {
      x: comp.frame.x ?? 0,
      y: comp.frame.y ?? 0,
      width: comp.frame.width ?? 0,
      height: comp.frame.height ?? 0,
      anchor: comp.frame.anchor ?? 'top_left',
      z: comp.frame.z ?? 0,
    },
    constraints: comp.constraints ?? {},
    style: { ...comp.style, token: comp.style?.token ?? null },
    typography: comp.typography ?? null,
  }));
  normalized.sort((a, b) => (a.frame.z - b.frame.z) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return {
    version: 2,
    canvas: { widthPx: params.canvas.widthPx, heightPx: params.canvas.heightPx },
    layers: normalized.map((c) => ({ id: c.id, z: c.frame.z })),
    components: normalized,
    degradations: params.degradations ?? [],
  };
}

/** C2 IR 结构校验（与 Python validate_layout_ir 同语义；fail-closed）。 */
export function validateLayoutIr(ir: LayoutIr): string[] {
  const issues: string[] = [];
  if (ir.version !== LAYOUT_IR_VERSION) issues.push('version 必须为 2');
  const { widthPx: cw, heightPx: ch } = ir.canvas;
  if (!Number.isInteger(cw) || cw <= 0) issues.push('canvas.widthPx 必须为正整数');
  if (!Number.isInteger(ch) || ch <= 0) issues.push('canvas.heightPx 必须为正整数');

  if (ir.components.length > 32) issues.push('components 超上界（32）');
  const seenIds = new Set<string>();
  for (const comp of ir.components) {
    const cid = comp.id;
    if (!cid || seenIds.has(cid)) issues.push(`组件 id 重复/为空: ${JSON.stringify(cid)}`);
    seenIds.add(cid);
    if (!LAYOUT_IR_COMPONENT_KINDS.includes(comp.kind)) {
      issues.push(`${cid}: kind 非法 ${JSON.stringify(comp.kind)}`);
    }
    if (!LAYOUT_IR_ROLES.includes(comp.role)) {
      issues.push(`${cid}: role 非法 ${JSON.stringify(comp.role)}`);
    }
    const { x, y, width: w, height: h } = comp.frame;
    if ([x, y, w, h].some((v) => typeof v !== 'number' || v < 0)) {
      issues.push(`${cid}: frame 几何必须非负`);
    }
    if (x + w > cw + 1e-6 || y + h > ch + 1e-6) issues.push(`${cid}: frame 超出画布`);
    const z = comp.frame.z;
    // 同层并列（z 相同）合法 —— 同层内序由 id 字典序稳定排（跨语言契约）。
    if (!Number.isInteger(z)) issues.push(`${cid}: z 层级缺失/非整数`);
    if (!LAYOUT_IR_ANCHORS.includes(comp.frame.anchor)) issues.push(`${cid}: anchor 非法`);
    if (comp.typography !== null && !LAYOUT_IR_WRAP_MODES.includes(comp.typography.wrapMode)) {
      issues.push(`${cid}: typography.wrapMode 非法`);
    }
  }
  return issues;
}
