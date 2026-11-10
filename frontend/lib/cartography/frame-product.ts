/**
 * G15 多画幅产品前端契约（frame_product.v1）。
 *
 * 单一真相 = committed MapSpec（layout.frames 画幅身份 + frame_product
 * 产品语义）。本模块只做投影与用户面写入（set_frame_product +
 * expected_revision CAS，409 superseded 静默收敛 —— 与 component-mutation
 * 同门）；导出路由决策如实披露客户端不可等价完成的语义（layerOverrides/
 * pageSize/time_window 精确性 → 后端 publication 产品页）。
 */
import { ApiError, apiFetch } from '@/lib/api/transport';
import {
  commitMapSpecDocument,
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  setMapSpecRevision,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { enqueueUserMutation } from '@/lib/mapspec/user-mutation';
import { devOnly } from '@/lib/utils/logger';
import type { FrameProductConfig, MapSpec } from '@/lib/mapspec-compiler/types';

export interface FrameProductSlotView {
  frameId: string;
  /** 画幅登记标题（layout.frames[].title）—— 显示序 = 产品帧序。 */
  title: string;
  enabled: boolean;
  legend: string;
  syncGroup: string | null;
  active: boolean;
  binding: {
    ref: string | null;
    layerId: string | null;
    field: string | null;
    contentRevision: number | null;
    hasWhere: boolean;
    hasTimeWindow: boolean;
  } | null;
}

export interface FrameProductView {
  bound: boolean;
  enabled: boolean;
  productKind: string | null;
  gridTitle: string;
  columns: number;
  activeFrame: string | null;
  slots: FrameProductSlotView[];
  sharedExpression: {
    field: string;
    domainStatus: string;
    breaksCount: number;
    k: number;
    palette: string;
  } | null;
  disclosures: string[];
}

export function projectFrameProduct(spec: MapSpec | null | undefined): FrameProductView {
  const product = spec?.frame_product;
  const layoutFrames = Array.isArray(spec?.layout?.frames) ? spec!.layout!.frames! : [];
  const byFrameId = new Map<string, { title?: string; enabled?: boolean }>();
  for (const f of layoutFrames) {
    if (f && typeof f === 'object' && typeof (f as { id?: unknown }).id === 'string') {
      const frame = f as { id: string; title?: string; enabled?: boolean };
      byFrameId.set(frame.id, frame);
    }
  }
  const empty: FrameProductView = {
    bound: false, enabled: false, productKind: null, gridTitle: '', columns: 1,
    activeFrame: null, slots: [], sharedExpression: null, disclosures: [],
  };
  if (!product || !isFrameProductConfig(product)) return empty;
  const frames = Array.isArray(product.frames) ? product.frames : [];
  const slots: FrameProductSlotView[] = [];
  for (const raw of frames) {
    if (!raw || typeof raw.frame_id !== 'string') continue;
    const registered = byFrameId.get(raw.frame_id);
    const data = raw.data;
    slots.push({
      frameId: raw.frame_id,
      title: raw.title ?? registered?.title ?? raw.frame_id,
      enabled: registered?.enabled !== false,
      legend: typeof raw.legend === 'string' ? raw.legend : 'shared',
      syncGroup: typeof raw.sync_group === 'string' ? raw.sync_group : null,
      active: product.active_frame === raw.frame_id,
      binding: data
        ? {
            ref: typeof data.ref === 'string' ? data.ref : null,
            layerId: typeof data.layer_id === 'string' ? data.layer_id : null,
            field: typeof data.field === 'string' ? data.field : null,
            contentRevision:
              typeof data.content_revision === 'number' ? data.content_revision : null,
            hasWhere: !!data.where,
            hasTimeWindow: !!data.time_window,
          }
        : null,
    });
  }
  const shared = isSharedExpression(product.shared_expression)
    ? {
        field: String(product.shared_expression.field ?? ''),
        domainStatus: String(product.shared_expression.domain_status ?? 'unknown'),
        breaksCount: Array.isArray(product.shared_expression.breaks)
          ? product.shared_expression.breaks.length
          : 0,
        k: typeof product.shared_expression.k === 'number' ? product.shared_expression.k : 0,
        palette: String(product.shared_expression.palette ?? ''),
      }
    : null;
  return {
    bound: true,
    enabled: product.enabled !== false,
    productKind: typeof product.product_kind === 'string' ? product.product_kind : null,
    gridTitle: product.grid?.title ?? '',
    columns: typeof product.grid?.columns === 'number' ? product.grid.columns : 1,
    activeFrame: typeof product.active_frame === 'string' ? product.active_frame : null,
    slots,
    sharedExpression: shared,
    disclosures: Array.isArray(product.disclosures)
      ? product.disclosures.filter((d): d is string => typeof d === 'string')
      : [],
  };
}

function isFrameProductConfig(v: unknown): v is FrameProductConfig {
  return !!v && typeof v === 'object' && Array.isArray((v as FrameProductConfig).frames);
}

function isSharedExpression(
  v: FrameProductConfig['shared_expression'],
): v is NonNullable<FrameProductConfig['shared_expression']> {
  return !!v && typeof v === 'object';
}

// ── 用户面写入（set_frame_product + CAS）───────────────────────────────────

interface MutationResponse {
  success?: boolean;
  status?: string;
  mutation_revision?: number;
  mapspec?: Record<string, unknown>;
  correction_hint?: string;
}

function supersededFromError(err: unknown): MutationResponse | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body as { detail?: MutationResponse } | MutationResponse | null;
  if (body && typeof body === 'object' && 'detail' in body && body.detail) return body.detail;
  return (body as MutationResponse) ?? null;
}

export interface FrameProductPatch {
  active_frame?: string;
  frames?: Array<Record<string, unknown>>;
  grid?: Record<string, unknown>;
}

/**
 * 键级 patch 提交（用户面）。409 superseded 静默收敛：回灌服务端真相，
 * 面板重渲染后用户基于新 revision 重试（与拖拽组件同语义）。
 */
export type FrameProductPatchOutcome = { status: 'applied' | 'superseded' };

/**
 * 键级 patch 提交（POST mapspec/mutations intent=set_frame_product）。
 * 成功 → setMapSpecRevision + commitMapSpecDocument（面板立即重渲）；
 * 409 superseded → 回灌服务端真相后静默返回（与组件拖拽同语义 ——
 * 用户下一次编辑基于新 revision 重试）。经共享串行链提交（revision
 * 在轮到本笔时才读）。
 */
export async function commitFrameProductPatch(
  patch: FrameProductPatch,
): Promise<FrameProductPatchOutcome | void> {
  const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
  await enqueueUserMutation(async () => {
    const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId || sessionId !== enqueuedSessionId) return;
    try {
      const data = await apiFetch<MutationResponse>(
        `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/mapspec/mutations`,
        {
          method: 'POST',
          body: {
            intent: 'set_frame_product',
            expected_revision: revision,
            patch,
          },
          ownerToken,
          label: 'MapSpec frame_product patch mutation',
        },
      );
      if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return;
      if (typeof data.mutation_revision === 'number') {
        setMapSpecRevision(data.mutation_revision);
      }
      commitMapSpecDocument(data.mapspec, data.mutation_revision);
      return { status: 'applied' };
    } catch (err) {
      const superseded = supersededFromError(err);
      if (!superseded) throw err;
      if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) return;
      if (typeof superseded.mutation_revision === 'number') {
        setMapSpecRevision(superseded.mutation_revision);
      }
      commitMapSpecDocument(superseded.mapspec, superseded.mutation_revision);
      devOnly.warn('[frame-product] patch superseded, converged to server truth');
      return { status: 'superseded' };
    }
  });
}

// ── 导出路由决策（诚实边界）───────────────────────────────────────────────

export interface FrameProductExportDecision {
  /** backend-publication：经 /export/vector-pdf atlas.driver=product（精确
   * 语义：layerOverrides/time_window/共享分级/图例组合）。 */
  backendPublication: boolean;
  /** 客户端栅格网格导出不可承载的语义（如实披露，不返回近似图冒充）。 */
  limitations: string[];
}

export function frameProductExportDecision(
  spec: MapSpec | null | undefined,
  format: string,
): FrameProductExportDecision {
  const product = spec?.frame_product;
  if (!product || product.enabled === false) {
    return { backendPublication: false, limitations: [] };
  }
  if (format === 'pdf' || format === 'vector-pdf') {
    return { backendPublication: true, limitations: [] };
  }
  // png/svg：客户端 canvas 网格可承载 where 过滤/范围/标题；不可等价承载
  // layerOverrides 样式覆写、精确纸型与共享图例带 —— 如实列出。
  return {
    backendPublication: false,
    limitations: [
      'client_canvas_no_layer_overrides',
      'client_canvas_no_shared_legend_band',
      'client_canvas_no_page_size',
    ],
  };
}

// ── 订阅面（面板/视图消费）───────────────────────────────────────────────

export function subscribeFrameProduct(
  cb: () => void,
): () => void {
  return subscribeMapSpecLive(cb);
}

/**
 * useSyncExternalStore 快照（identity 缓存）：committed spec 对象引用
 * 不变 ⇒ 同一 view 引用（内联分配会让 snapshot 永不稳定 → 无限重渲）。
 */
let _snapshotCache: { spec: MapSpec | null; view: FrameProductView } | null = null;

export function getFrameProductSnapshot(): FrameProductView {
  const spec = getCommittedMapSpec();
  if (_snapshotCache && _snapshotCache.spec === spec) {
    return _snapshotCache.view;
  }
  const view = projectFrameProduct(spec);
  _snapshotCache = { spec, view };
  return view;
}
