/**
 * G34 纸面作者模型 TS 镜像 + 工作台状态机。
 *
 * Python 权威：app/lib/cartography/print_layout_schema.py (print_layout.v1)
 * 保存位置唯一 = MapSpec.layout.print_layout（随 map-state 恢复）。
 * 本文件**不**建第二套 localStorage 作者文档；localStorage 只存会话 anchor。
 *
 * 状态机复用 G20 的 draft/preview/commit 模式，额外加 generation guard：
 * 页面/会话切换后旧缩略图或字体请求返回时，generation 不匹配即丢弃，
 * 不覆盖当前页（验收场景 19）。
 */

export const PRINT_LAYOUT_SCHEMA_VERSION = 'print_layout.v1' as const;
export const MAX_PRINT_COMPONENTS = 32;
export const MAX_ATLAS_PAGES = 20;
export const MAX_TEXT_BYTES = 2048;

export type FontId =
  | 'noto-sans-sc-embedded' | 'noto-sans-cjk-sc'
  | 'noto-serif-cjk-sc' | 'dejavu-sans';

export type ComponentKind =
  | 'title' | 'subtitle' | 'legend' | 'scale_bar' | 'north_arrow'
  | 'attribution' | 'disclosure' | 'map_slot' | 'text_block'
  | 'page_number' | 'toc' | 'group';

export type OverflowPolicy = 'wrap' | 'expand' | 'continue' | 'block';
export type Align = 'left' | 'center' | 'right';

export interface FrameMm { x: number; y: number; width: number; height: number }

export interface Typography {
  font_id: FontId;
  size_pt: number;
  line_height: number;
  weight: 'normal' | 'bold';
  style: 'normal' | 'italic';
  align: Align;
  letter_spacing_pt: number;
  color: string;
  wrap_mode: 'word' | 'char' | 'none';
  overflow_policy: OverflowPolicy;
}

export interface LegendLayout {
  columns: number;
  column_gap_mm: number;
  row_gap_mm: number;
  swatch_mm: number;
  symbol_mm: number;
  group_by_layer: boolean;
  continuation: 'expand' | 'continue' | 'block';
  title_wrap: boolean;
}

export interface PrintText {
  mode: 'literal' | 'binding';
  value: string;
  binding: 'page_title' | 'page_number' | 'page_count' | 'data_time'
    | 'unit' | 'source_attribution' | 'map_title' | 'map_subtitle';
  format: 'plain' | 'upper' | 'lower' | 'padded2';
  missing_placeholder: string;
}

export interface PrintComponent {
  id: string;
  kind: ComponentKind;
  frame: FrameMm;
  z: number;
  group_id?: string | null;
  locked: boolean;      // 只读投影；唯一事实 = mapspec.workbench.lockedComponentIds
  visible: boolean;
  typography?: Typography;
  text?: PrintText;
  legend?: LegendLayout;
  map_slot?: { frame_id?: string | null; frame_index?: number | null; fit: 'contain' | 'crop' };
}

export interface PrintPaper {
  profile: string;
  width_mm: number;
  height_mm: number;
  orientation: 'landscape' | 'portrait';
}

export interface PrintPage {
  page_id: string;
  enabled: boolean;
  order: number;
  title: string;
  original_title?: string;
  paper_profile?: string | null;
  template_id?: string | null;
  overrides: Array<Record<string, unknown>>;
  binding_digest: string;
}

export interface PrintLayoutV1 {
  version: 'print_layout.v1';
  paper: PrintPaper;
  safe_area: { top: number; right: number; bottom: number; left: number };
  source: { mapspec_revision: number; style_revision: string; spec_fingerprint: string };
  font_asset_version: string;
  render_path: 'publication_ir_svg_v1';
  template_id?: string | null;
  template_version: number;
  components: PrintComponent[];
  legend_defaults: LegendLayout;
  atlas: { pages: PrintPage[]; toc: boolean; page_number_start: number };
  templates: Array<Record<string, unknown>>;
  updated_at: string;
}

export const DEFAULT_TYPOGRAPHY: Typography = {
  font_id: 'noto-sans-sc-embedded', size_pt: 9, line_height: 1.35,
  weight: 'normal', style: 'normal', align: 'left', letter_spacing_pt: 0,
  color: '#1e293b', wrap_mode: 'word', overflow_policy: 'expand',
};

export const DEFAULT_LEGEND_LAYOUT: LegendLayout = {
  columns: 1, column_gap_mm: 4, row_gap_mm: 1.5, swatch_mm: 4,
  symbol_mm: 6, group_by_layer: false, continuation: 'expand', title_wrap: true,
};

/** 与后端 empty_print_layout() 同款 A4 landscape 初始计划。 */
export function defaultPrintLayout(): PrintLayoutV1 {
  const comp = (
    id: string, kind: ComponentKind, frame: FrameMm,
    extra: Partial<PrintComponent> = {},
  ): PrintComponent => ({
    id, kind, frame, z: 0, locked: false, visible: true, ...extra,
  });
  return {
    version: PRINT_LAYOUT_SCHEMA_VERSION,
    paper: { profile: 'a4_landscape', width_mm: 297, height_mm: 210, orientation: 'landscape' },
    safe_area: { top: 5, right: 5, bottom: 5, left: 5 },
    source: { mapspec_revision: 0, style_revision: '', spec_fingerprint: '' },
    font_asset_version: '',
    render_path: 'publication_ir_svg_v1',
    template_id: null,
    template_version: 0,
    components: [
      comp('title', 'title', { x: 15, y: 10, width: 180, height: 18 }, {
        typography: { ...DEFAULT_TYPOGRAPHY, size_pt: 18, weight: 'bold', align: 'center' },
        text: {
          mode: 'binding', value: '', binding: 'map_title', format: 'plain',
          missing_placeholder: '〔数据缺失〕',
        },
      }),
      comp('map', 'map_slot', { x: 10, y: 34, width: 277, height: 150 }, {
        map_slot: { fit: 'contain' },
      }),
      comp('legend', 'legend', { x: 15, y: 186, width: 110, height: 14 }, {
        legend: { ...DEFAULT_LEGEND_LAYOUT, columns: 2 },
      }),
      comp('scale', 'scale_bar', { x: 210, y: 186, width: 72, height: 12 }),
      comp('attribution', 'attribution', { x: 15, y: 198, width: 180, height: 5 }, {
        typography: { ...DEFAULT_TYPOGRAPHY, size_pt: 7 },
        text: {
          mode: 'binding', value: '', binding: 'source_attribution',
          format: 'plain', missing_placeholder: '〔数据缺失〕',
        },
      }),
    ],
    legend_defaults: { ...DEFAULT_LEGEND_LAYOUT },
    atlas: { pages: [], toc: false, page_number_start: 1 },
    templates: [],
    updated_at: '',
  };
}

/** 关闭词表 + 有界前端预检（后端 validate_print_layout 是权威）。 */
export function draftFieldErrors(plan: PrintLayoutV1 | null): string[] {
  if (!plan) return ['print_layout_missing'];
  const errors: string[] = [];
  if (plan.version !== PRINT_LAYOUT_SCHEMA_VERSION) errors.push('unknown_version');
  if (plan.components.length > MAX_PRINT_COMPONENTS) errors.push('component_budget');
  if (new Set(plan.components.map((c) => c.id)).size !== plan.components.length) {
    errors.push('duplicate_component_id');
  }
  const w = plan.paper.width_mm;
  const h = plan.paper.height_mm;
  const sa = plan.safe_area;
  for (const c of plan.components) {
    const f = c.frame;
    if (![f.x, f.y, f.width, f.height].every(Number.isFinite)) {
      errors.push(`invalid_frame:${c.id}`); continue;
    }
    if (f.width <= 0 || f.height <= 0) errors.push(`invalid_frame:${c.id}`);
    if (f.x + f.width > w + 0.01 || f.y + f.height > h + 0.01) {
      errors.push(`outside_paper:${c.id}`);
    } else if (f.x < sa.left - 0.01 || f.y < sa.top - 0.01
      || f.x + f.width > w - sa.right + 0.01
      || f.y + f.height > h - sa.bottom + 0.01) {
      errors.push(`outside_safe_area:${c.id}`);
    }
    if (c.text && new TextEncoder().encode(c.text.value).length > MAX_TEXT_BYTES) {
      errors.push(`text_too_long:${c.id}`);
    }
    if (c.typography && (c.typography.size_pt < 4 || c.typography.size_pt > 96)) {
      errors.push(`invalid_font_size:${c.id}`);
    }
    if (c.legend && (c.legend.columns < 1 || c.legend.columns > 4)) {
      errors.push(`invalid_legend_columns:${c.id}`);
    }
  }
  if (plan.atlas.pages.filter((p) => p.enabled).length > MAX_ATLAS_PAGES) {
    errors.push('page_budget');
  }
  return errors;
}

export function isDraftDirty(committed: PrintLayoutV1 | null,
                             draft: PrintLayoutV1 | null): boolean {
  if (!committed || !draft) return Boolean(committed) !== Boolean(draft);
  return JSON.stringify(committed) !== JSON.stringify(draft);
}

// ── 工作台状态机 ──────────────────────────────────────────────────────────

export type PrintLayoutPhase =
  | 'idle' | 'loading' | 'committed' | 'draft' | 'preview'
  | 'exporting' | 'blocked' | 'font_unavailable' | 'cancelled' | 'failed';

export interface PrintLayoutState {
  phase: PrintLayoutPhase;
  generation: number;
  pageId: string;
  committed: PrintLayoutV1 | null;
  draft: PrintLayoutV1 | null;
  selectedIds: string[];
  previewSvg: string | null;
  previewReceipt: Record<string, unknown> | null;
  previewFingerprint: string | null;
  dpi: number;
  zoom: number;
  revision: number;
  busy: boolean;
  errors: string[];
  warnings: string[];
  exportRefs: { download_url?: string; manifest_url?: string; format?: string } | null;
}

export type PrintLayoutAction =
  | { type: 'load-start'; generation?: number }
  | { type: 'load-ok'; plan: PrintLayoutV1 | null; revision: number; generation: number }
  | { type: 'load-failed'; error: string; generation: number }
  | { type: 'select'; ids: string[] }
  | { type: 'patch-component'; id: string; patch: Partial<PrintComponent> }
  | { type: 'patch-plan'; patch: Partial<PrintLayoutV1> }
  | { type: 'restore-committed' }
  | { type: 'page-switch'; pageId: string }
  | { type: 'preview-start' }
  | { type: 'preview-ok'; svg: string; receipt: Record<string, unknown>;
      fingerprint: string; generation: number }
  | { type: 'preview-failed'; error: string; generation: number;
      fontUnavailable?: boolean }
  | { type: 'apply-start' }
  | { type: 'apply-ok'; plan: PrintLayoutV1; revision: number }
  | { type: 'apply-failed'; error: string }
  | { type: 'export-start' }
  | { type: 'export-ok'; refs: PrintLayoutState['exportRefs'] }
  | { type: 'export-failed'; error: string }
  | { type: 'cancel' }
  | { type: 'dpi'; dpi: number }
  | { type: 'zoom'; zoom: number };

export function initialPrintLayoutState(): PrintLayoutState {
  return {
    phase: 'idle', generation: 0, pageId: '', committed: null, draft: null,
    selectedIds: [], previewSvg: null, previewReceipt: null,
    previewFingerprint: null, dpi: 300, zoom: 1, revision: 0, busy: false,
    errors: [], warnings: [], exportRefs: null,
  };
}

/** generation 不匹配的异步回包一律丢弃（页面/会话切换后的旧请求）。 */
export function reducePrintLayout(
  state: PrintLayoutState, action: PrintLayoutAction,
): PrintLayoutState {
  switch (action.type) {
    case 'load-start':
      return { ...state, phase: 'loading', busy: true, errors: [] };
    case 'load-ok':
      if (action.generation !== state.generation) return state;
      return {
        ...state, phase: action.plan ? 'committed' : 'idle', busy: false,
        committed: action.plan, draft: action.plan ? structuredClone(action.plan) : null,
        revision: action.revision, previewSvg: null, previewReceipt: null,
        errors: [], warnings: [],
      };
    case 'load-failed':
      if (action.generation !== state.generation) return state;
      return { ...state, phase: 'failed', busy: false, errors: [action.error] };
    case 'select':
      return { ...state, selectedIds: action.ids };
    case 'patch-component': {
      if (!state.draft) return state;
      const draft = structuredClone(state.draft);
      const idx = draft.components.findIndex((c) => c.id === action.id);
      if (idx < 0) return state;
      draft.components[idx] = { ...draft.components[idx], ...action.patch };
      return { ...state, draft, phase: 'draft', previewSvg: null, errors: [] };
    }
    case 'patch-plan': {
      if (!state.draft) return state;
      return {
        ...state, draft: { ...structuredClone(state.draft), ...action.patch },
        phase: 'draft', previewSvg: null, errors: [],
      };
    }
    case 'restore-committed':
      return {
        ...state, draft: state.committed ? structuredClone(state.committed) : null,
        phase: state.committed ? 'committed' : 'idle', previewSvg: null,
        errors: [], warnings: [],
      };
    case 'page-switch':
      // 页面/会话切换：递增 generation，作废所有在途缩略图/字体请求。
      return {
        ...state, generation: state.generation + 1, pageId: action.pageId,
        previewSvg: null, previewReceipt: null, previewFingerprint: null,
        busy: false, errors: [], warnings: [],
      };
    case 'preview-start':
      return { ...state, busy: true, phase: 'preview', errors: [] };
    case 'preview-ok':
      if (action.generation !== state.generation) return state;
      return {
        ...state, busy: false, phase: 'preview', previewSvg: action.svg,
        previewReceipt: action.receipt, previewFingerprint: action.fingerprint,
      };
    case 'preview-failed':
      if (action.generation !== state.generation) return state;
      return {
        ...state, busy: false,
        phase: action.fontUnavailable ? 'font_unavailable' : 'blocked',
        errors: [action.error], previewSvg: null,
      };
    case 'apply-start':
      return { ...state, busy: true, errors: [] };
    case 'apply-ok':
      return {
        ...state, busy: false, phase: 'committed', committed: action.plan,
        draft: structuredClone(action.plan), revision: action.revision,
        errors: [], previewSvg: null,
      };
    case 'apply-failed':
      return { ...state, busy: false, phase: 'blocked', errors: [action.error] };
    case 'export-start':
      return { ...state, busy: true, phase: 'exporting', errors: [] };
    case 'export-ok':
      return { ...state, busy: false, phase: 'committed', exportRefs: action.refs };
    case 'export-failed':
      return { ...state, busy: false, phase: 'blocked', errors: [action.error] };
    case 'cancel':
      return {
        ...state, busy: false, phase: 'cancelled', previewSvg: null,
        errors: ['cancelled'],
      };
    case 'dpi':
      return { ...state, dpi: action.dpi, previewSvg: null };
    case 'zoom':
      // 只改屏幕显示比例；持久毫米坐标（draft.plan）完全不变。
      return { ...state, zoom: action.zoom };
    default:
      return state;
  }
}
