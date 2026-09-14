/**
 * Canvas affordance 协议（ADR-0194，与 docs/dev/mixed-initiative-copilot-spec.md
 * 同步；后端权威实现 app/services/gis_situation/canvas_affordance.py）。
 *
 * 「画布即 Prompt」：前端画布手势 → SpatialAffordanceEnvelope →
 *   ① 即时 POST /api/v1/chat/sessions/{id}/canvas-actions（fire-and-forget，
 *      < 100ms 同步预算）；
 *   ② 同时 stage 进 store，随下一轮 /chat/stream 请求体 canvas_actions 捎带
 *      （服务端内容寻重防双计）。
 *
 * 「生成式微 UI」：SSE ui_action.mount_widget → isWidgetSpecSafe（最后一道
 *   双向校验的前端侧）→ copilotSlice.pushCopilotWidget。
 */
import { apiFetch } from '@/lib/api/transport';

/* ─── 封闭词表 ─── */

export type CanvasActionKind =
  | 'box_select'
  | 'freehand_lasso'
  | 'polygon_lasso'
  | 'highlight'
  | 'measure'
  | 'snap_pick'
  | 'widget_reply';

export type WidgetKind =
  | 'histogram_slider'
  | 'swipe_compare'
  | 'candidate_picker'
  | 'sketch_box';

export const WIDGET_KINDS: readonly WidgetKind[] = [
  'histogram_slider',
  'swipe_compare',
  'candidate_picker',
  'sketch_box',
];

/* ─── 信封 / 动作 ─── */

export interface CanvasAction {
  action_id: string;
  kind: CanvasActionKind;
  geometry?: { type: string; coordinates: unknown };
  bbox?: [number, number, number, number];
  screen_px?: { x: number; y: number; width?: number; height?: number };
  layer_refs?: string[];
  map_view?: { center: [number, number]; zoom: number };
  created_at?: number;
  meta?: Record<string, unknown>;
}

export interface SpatialAffordanceEnvelope {
  envelope_id: string;
  client_generation?: number;
  client_ts: number;
  actions: CanvasAction[];
}

export interface WidgetReply {
  widget_id: string;
  kind: WidgetKind;
  value: unknown;
}

function newId(prefix: string): string {
  const c = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `${prefix}-${c}`;
}

/** 环（像素或经纬度对数组）→ GeoJSON Polygon（自动闭合）。 */
export function ringToPolygon(
  ring: [number, number][],
): { type: 'Polygon'; coordinates: [number, number][][] } | undefined {
  if (ring.length < 3) return undefined;
  const [first, last] = [ring[0], ring[ring.length - 1]];
  const closed =
    first[0] === last[0] && first[1] === last[1] ? ring : [...ring, first];
  return { type: 'Polygon', coordinates: [closed] };
}

export function buildCanvasAction(
  kind: CanvasActionKind,
  opts: Partial<Omit<CanvasAction, 'action_id' | 'kind'>> = {},
): CanvasAction {
  return {
    action_id: newId('act'),
    kind,
    created_at: Date.now(),
    ...opts,
  };
}

export function buildEnvelope(actions: CanvasAction[]): SpatialAffordanceEnvelope {
  return {
    envelope_id: newId('env'),
    client_ts: Date.now(),
    actions,
  };
}

/* ─── widget 安全校验（mount 前最后一道，与后端 validate_widget_spec 同规则）─── */

const FORBIDDEN_KEYS = new Set([
  'script', 'iframe', 'object', 'embed', 'html', 'innerhtml', 'srcdoc',
]);
const FORBIDDEN_VALUE_MARKS = [
  'javascript:', 'data:text/html', '<script', '</script', '<iframe',
];

/** 递归白名单扫描：声明式数据之外的一切（可执行键/值、宿主对象）→ false。 */
export function isWidgetSpecSafe(value: unknown, depth = 0): boolean {
  if (depth > 12) return false;
  if (value === null || typeof value === 'boolean' || typeof value === 'number') {
    return true;
  }
  if (typeof value === 'string') {
    const lowered = value.toLowerCase();
    return !FORBIDDEN_VALUE_MARKS.some((mark) => lowered.includes(mark));
  }
  if (Array.isArray(value)) {
    return value.slice(0, 256).every((item) => isWidgetSpecSafe(item, depth + 1));
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>).every(([key, item]) => {
      const loweredKey = key.toLowerCase();
      if (FORBIDDEN_KEYS.has(loweredKey)) return false;
      if (loweredKey.startsWith('on') && loweredKey.length > 2) return false;
      return isWidgetSpecSafe(item, depth + 1);
    });
  }
  return false; // function / symbol / undefined / bigint 等一律拒绝
}

export interface WidgetSpec {
  widget_id: string;
  kind: WidgetKind;
  title?: string;
  payload: Record<string, unknown>;
  expires_at?: number;
}

/**
 * SSE ui_action 载荷 → 安全 WidgetSpec；任何不合规 → null（丢弃 + warn）。
 */
export function extractMountedWidget(data: unknown): WidgetSpec | null {
  if (typeof data !== 'object' || data === null) return null;
  const record = data as Record<string, unknown>;
  if (record.action !== 'mount_widget') return null;
  const raw = record.widget;
  if (typeof raw !== 'object' || raw === null || !isWidgetSpecSafe(raw)) {
    console.warn('[copilot] unsafe or malformed widget dropped');
    return null;
  }
  const spec = raw as Record<string, unknown>;
  if (
    typeof spec.widget_id !== 'string' ||
    !spec.widget_id ||
    typeof spec.kind !== 'string' ||
    !WIDGET_KINDS.includes(spec.kind as WidgetKind) ||
    typeof spec.payload !== 'object' ||
    spec.payload === null
  ) {
    console.warn('[copilot] widget spec failed shape check, dropped');
    return null;
  }
  return {
    widget_id: spec.widget_id,
    kind: spec.kind as WidgetKind,
    title: typeof spec.title === 'string' ? spec.title : '',
    payload: spec.payload as Record<string, unknown>,
    ...(typeof spec.expires_at === 'number' ? { expires_at: spec.expires_at } : {}),
  };
}

/* ─── 上报通道 ─── */

interface AffordanceChannel {
  getSessionId: () => string | null;
  getOwnerToken?: () => string | null;
}

let channel: AffordanceChannel | null = null;

/** 由会话持有方（use-sse-stream）注册；未注册时上报退化为仅 stage。 */
export function configureAffordanceChannel(chan: AffordanceChannel): void {
  channel = chan;
}

export interface AffordanceReportResult {
  /** 同步路径耗时（信封构建 + fetch 发起），预算 < 100ms（spec §5）。 */
  latencyMs: number;
  dispatched: boolean;
}

/**
 * 立即上报一封画布动作信封（fire-and-forget：不 await 网络完成）。
 *
 * staging（随下一轮 turn 捎带）由调用方负责 —— store 归调用方读写，
 * 本模块保持无依赖纯通道（避免与 useHudStore 的模块初始化环）。
 */
export function reportAffordance(
  envelope: SpatialAffordanceEnvelope,
): AffordanceReportResult {
  const started = typeof performance !== 'undefined' ? performance.now() : Date.now();
  let dispatched = false;
  const sessionId = channel?.getSessionId() ?? null;
  if (sessionId && typeof window !== 'undefined' && typeof window.fetch === 'function') {
    const url = `${window.location.origin}/api/v1/chat/sessions/${encodeURIComponent(
      sessionId,
    )}/canvas-actions`;
    const ownerToken = channel?.getOwnerToken?.() ?? null;
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (ownerToken) headers['X-Session-Token'] = ownerToken;
    // fire-and-forget：交互上报绝不阻塞手势线程；失败静默（下一轮 turn
    // 捎带的同一信封经服务端内容寻重补齐，不丢语义）。
    void window
      .fetch(url, { method: 'POST', headers, body: JSON.stringify(envelope) })
      .catch(() => undefined);
    dispatched = true;
  }
  const ended = typeof performance !== 'undefined' ? performance.now() : Date.now();
  return { latencyMs: ended - started, dispatched };
}

/** 即时端点响应（ack 投影，仅遥测用）。 */
export interface CanvasActionsAck {
  accepted?: boolean;
  reason?: string;
  sequence?: number;
  accepted_actions?: string[];
  rejected_actions?: Array<{ action_id: string; reason: string }>;
}

/** 显式 await 版（诊断/测试用）；生产交互路径走 reportAffordance。 */
export function reportCanvasActions(
  sessionId: string,
  envelope: SpatialAffordanceEnvelope,
  ownerToken?: string | null,
): Promise<CanvasActionsAck> {
  return apiFetch<CanvasActionsAck>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/canvas-actions`,
    {
      method: 'POST',
      body: envelope as unknown as Record<string, unknown>,
      ...(ownerToken ? { ownerToken } : {}),
      label: 'Canvas affordance report error',
    },
  );
}
