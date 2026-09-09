/**
 * Collab 事件落地（Workbench V6）—— 把入站事件变成 store/committed spec
 * 变更。落地纪律（ADR-0119）：
 * - doc/delta 事件：远端是**服务器已提交真相** → 归一化后水合 store + 以
 *   其为提交基线（revision 单调采纳；更旧 = 已被 CAS 淘汰的写者，忽略）。
 * - presentation 事件：单层轻量同步（免全量 refetch）；同样 revision 门控。
 * - op 事件：跨浏览器 journal（本端 undo 栈不受影响 —— 只显示）。
 * - artifact 事件：stale ref 集合更新（投影端点对账兜底）。
 * - 主动对账：revision 缺口/心跳 mismatch → 新鲜读 workbench/state → 采纳。
 */
import { apiFetch } from '@/lib/api/transport';
import {
  commitMapSpecDocument,
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  setMapSpecRevision,
} from '@/lib/mapspec/session-cursor';
import { useHudStore } from '@/lib/store/useHudStore';
import { normalizeWorkbenchDoc } from '@/lib/workbench/doc';
import {
  applyRemoteWorkbenchDelta,
  hydrateRemoteWorkbenchDoc,
} from '@/lib/workbench/persistence';
import { devOnly } from '@/lib/utils/logger';
import { parseEnvelope, parseOpEntry } from './protocol';
import {
  collabApplyPresenceAction,
  collabMarkStaleRefs,
  collabPushRemoteOp,
  collabSetStaleRefs,
} from './store';
import {
  bindCollabPoll,
  bindCollabRefetch,
  setCollabKnownRevision,
  startCollabClient,
  stopCollabClient,
} from './client';

/** 远端 doc/delta 事件落地（revision 单调门控在 persistence 基线侧）。 */
function adoptRemoteDoc(doc: unknown, revision: number): boolean {
  const normalized = normalizeWorkbenchDoc(doc);
  if (normalized == null) return false;
  return hydrateRemoteWorkbenchDoc(normalized, revision);
}

/** 远端 presentation 事件落地：committed spec 单层 patch + HUD 回灌。 */
function adoptRemotePresentation(
  data: Record<string, unknown>,
  revision: number,
): boolean {
  const layerId = typeof data.layerId === 'string' ? data.layerId : '';
  if (layerId === '') return false;
  const visible = typeof data.visible === 'boolean' ? data.visible : undefined;
  const opacity = typeof data.opacity === 'number' ? data.opacity : undefined;
  if (visible === undefined && opacity === undefined) return false;
  setMapSpecRevision(revision);

  // 1) HUD 行回灌（source:'server' 保认证标签语义；无 op 才跳过）。
  const target = useHudStore.getState().layers.find((l) => l.id === layerId);
  if (target != null) {
    const next: { visible?: boolean; opacity?: number } = {};
    if (visible !== undefined && target.visible !== visible) next.visible = visible;
    if (opacity !== undefined && target.opacity !== opacity) next.opacity = opacity;
    if (Object.keys(next).length > 0) {
      useHudStore.getState().updateLayer(layerId, next, { source: 'server' });
    }
  }
  // 2) committed spec 层 patch（compose parity —— 下次 compose 不回退旧值）。
  patchCommittedSpecLayer(layerId, visible, opacity);
  return true;
}

function patchCommittedSpecLayer(
  layerId: string,
  visible?: boolean,
  opacity?: number,
): void {
  const spec = getCommittedMapSpec() as unknown as { layers?: Record<string, unknown>[] } | null;
  if (spec == null || !Array.isArray(spec.layers)) return;
  let changed = false;
  const layers = spec.layers.map((layer) => {
    const candidate = layer as {
      id?: string;
      layout?: Record<string, unknown>;
      paint?: Record<string, unknown>;
    };
    if (candidate.id !== layerId) return layer;
    changed = true;
    const next: Record<string, unknown> = { ...layer };
    if (visible !== undefined) {
      next.layout = { ...(candidate.layout ?? {}), visibility: visible ? 'visible' : 'none' };
    }
    if (opacity !== undefined) {
      next.paint = { ...(candidate.paint ?? {}), opacity };
    }
    return next;
  });
  if (!changed) return;
  // 无 revision 参数（迟到保护已在调用方以 revision 门控完成）。
  commitMapSpecDocument({ ...(spec as object), layers } as unknown);
}

/** 远端 delta 本地应用：当前 store doc + delta → hydrate（revision 门控内）。 */
function applyRemoteDelta(rawDelta: unknown, revision: number): boolean {
  if (rawDelta == null || typeof rawDelta !== 'object') return false;
  const applied = applyRemoteWorkbenchDelta(rawDelta as never, revision);
  if (applied) return true;
  return false;
}

/** 总线事件主入口（client 的 onEvent 回调；event='bus'）。 */
export function handleBusEnvelope(raw: unknown): void {
  const envelope = parseEnvelope(raw);
  if (envelope == null) return;
  const { sessionId } = getMapSpecSessionCursor();
  if (sessionId == null || envelope.sid !== sessionId) return;
  const revision = typeof envelope.data.revision === 'number' ? envelope.data.revision : null;

  switch (envelope.kind) {
    case 'doc': {
      if (revision != null) setCollabKnownRevision(revision);
      if (envelope.truncated === true) {
        // 超预算 doc：事件只有对账锚点 → 新鲜读补齐。
        void reconcileWorkbenchState();
        return;
      }
      if (revision == null) return;
      adoptRemoteDoc(envelope.data.doc, revision);
      break;
    }
    case 'delta': {
      if (revision != null) setCollabKnownRevision(revision);
      // R1-M3：delta 事件只带 delta —— 在当前 store doc 上本地应用
      // （绝对值语义 → 重放幂等），免全量 refetch。引用漂移（delta 引用
      // 本地没有的组）才回退权威对账。
      if (revision == null) return;
      if (applyRemoteDelta(envelope.data.delta, revision)) return;
      void reconcileWorkbenchState();
      break;
    }
    case 'presentation': {
      if (revision == null) return;
      // R1-M7：不做全局 revision 门 —— 本端在途提交的 HTTP 响应可先把
      // cursor 推到 N+1，随后到达的他人 rev N presentation 事件是合法的
      // 单层绝对值事实（幂等），门控会丢事件。
      adoptRemotePresentation(envelope.data, revision);
      break;
    }
    case 'op': {
      const entry = parseOpEntry(envelope, envelope.seq);
      if (entry != null) collabPushRemoteOp(entry);
      break;
    }
    case 'presence': {
      collabApplyPresenceAction(String(envelope.data.action ?? 'update'), envelope.data);
      break;
    }
    case 'artifact': {
      const refId = typeof envelope.data.refId === 'string' ? envelope.data.refId : null;
      if (refId != null) collabMarkStaleRefs([refId], true);
      break;
    }
    default:
      break;
  }
}

/** 权威对账：新鲜读 workbench/state → 采纳 doc（revision 缺口/心跳 mismatch）。 */
export async function reconcileWorkbenchState(): Promise<boolean> {
  const { sessionId, ownerToken } = getMapSpecSessionCursor();
  if (sessionId == null) return false;
  try {
    const data = await apiFetch<{ revision?: number; doc?: unknown }>(
      `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/workbench/state`,
      { ownerToken: ownerToken ?? null },
    );
    if (typeof data.revision === 'number') {
      setCollabKnownRevision(data.revision);
      if (data.doc != null) adoptRemoteDoc(data.doc, data.revision);
      return true;
    }
    return false;
  } catch (err) {
    // 401 → apiFetch 已带一次 refresh 重试；仍失败 = 会话不可达，诚实保态。
    devOnly.warn('[collab-adopt] reconcile failed:', err);
    return false;
  }
}

/** artifact-status 对账（stale 集合全量刷新；事件流增量之外的兜底）。 */
export async function reconcileArtifactStatus(): Promise<void> {
  const { sessionId, ownerToken } = getMapSpecSessionCursor();
  if (sessionId == null) return;
  try {
    const data = await apiFetch<{ artifacts?: Array<{ artifactId?: string; status?: string }> }>(
      `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/workbench/artifact-status`,
      { ownerToken: ownerToken ?? null },
    );
    const stale = (data.artifacts ?? [])
      .filter((a) => a.status != null && a.status !== 'valid' && typeof a.artifactId === 'string')
      .map((a) => a.artifactId as string);
    // R2-M-7：以投影结果整体替换（re-composed/ref 刷新后的 un-stale 也收敛）。
    collabSetStaleRefs(stale);
  } catch (err) {
    devOnly.warn('[collab-adopt] artifact reconcile failed:', err);
  }
}

/* ─── 会话接线（use-workspace-session 在会话切换/挂载时调用）──────────── */

export function startWorkbenchCollabV6(sessionId: string): void {
  bindCollabRefetch(async () => {
    await reconcileWorkbenchState();
  });
  // C-2：降级/页面可见恢复时的对账兜底（正确性不依赖总线，但必须有探测路径）。
  bindCollabPoll(() => {
    void reconcileWorkbenchState();
    void reconcileArtifactStatus(); // R2-M-7：stale 集合对账兜底
  });
  // 初始基线由 client onopen 触发（无凭据/离线不发 fetch）。
  startCollabClient(sessionId, (event, data) => {
    if (event === 'doc') {
      // sync 回放应答（服务端发现 knownRevision 缺口时回放权威 doc）。
      const revision = typeof data.revision === 'number' ? data.revision : null;
      if (revision != null) {
        setCollabKnownRevision(revision);
        adoptRemoteDoc(data.doc, revision);
      }
      return;
    }
    if (event === 'presence_full') {
      // 参与者满员：显式披露（仍可收事件 = 只读观战）。
      devOnly.warn('[collab-adopt] participants full — read-only presence');
      return;
    }
    if (event === 'lease_result') {
      // 授权结果为服务端权威；租约快照由 hello/重连刷新，无本地第二真相。
      return;
    }
    if (event === 'pong' || event === 'hello' || event === 'sync_ok') {
      return; // client 内部已处理（revision 对账/快照）
    }
    if (event === 'bus') {
      handleBusEnvelope(data);
    }
  });
}

export function stopWorkbenchCollabV6(): void {
  stopCollabClient();
}
