/**
 * Collab WS client（Workbench V6）—— 服务端事件通道生命周期。
 *
 * 语义（ADR-0119）：
 * - 认证：认证会话 subprotocol ["bearer", <jwt>]；匿名会话 ["session",
 *   <owner_token>]（token 不落 URL/日志，与 ws.py 同法）。
 * - 重连：指数退避 0.5s→8s cap + jitter；重连成功即发 sync{knownRevision}
 *   对账（R1-M1/M2：漏事件/重启窗口由权威 refetch 闭合，正确性不依赖通道）。
 * - 心跳：10s ping；pong 携带服务端新鲜 revision → 与本地游标不一致即
 *   触发 refetch 对账（read-only 参与者也能收敛）。
 * - 预算：outbound 仅 presence（250ms coalesce）与 lease 操作 —— 正常使用
 *   远低于服务端 token bucket。
 * - 一切事件经 adopt 模块落地（本模块不做状态语义）。
 */
import { WS_BASE } from '@/lib/api/config';
import { getAccessToken } from '@/lib/auth/tokenStore';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { devOnly } from '@/lib/utils/logger';
import {
  collabSetLeases,
  collabSetParticipants,
  collabSetSessionIdentity,
  collabSetStatus,
  collabResetForSession,
} from './store';
import {
  parseLeaseInfo,
  parseParticipant,
  type CollabLeaseInfo,
  type CollabParticipant,
} from './protocol';

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 8000;
const HEARTBEAT_MS = 10_000;
const PRESENCE_COALESCE_MS = 250;

type InboundHandler = (event: string, data: Record<string, unknown>) => void;

let ws: WebSocket | null = null;
let boundSessionId: string | null = null;
let reconnectAttempt = 0;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
let stopped = true;
let handler: InboundHandler | null = null;
let lastKnownRevision = 0;

/** presence outbound 合并（250ms 窗口内的更新只发最后一帧）。 */
let presenceTimer: ReturnType<typeof setTimeout> | null = null;
let pendingPresence: Record<string, unknown> | null = null;

/** refetch 单飞 + 最小间隔（revision 对账通道；adopt 提供实现避免环）。 */
let refetchFn: (() => Promise<void>) | null = null;
let refetchInflight = false;
let lastRefetchAt = 0;

export function bindCollabRefetch(fn: () => Promise<void>): void {
  refetchFn = fn;
}

export function setCollabKnownRevision(revision: number): void {
  if (Number.isFinite(revision) && revision >= 0) lastKnownRevision = revision;
}

function backoffMs(): number {
  const exp = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * 2 ** reconnectAttempt);
  return exp / 2 + Math.random() * (exp / 2); // 抖动对称
}

function scheduleReconnect(): void {
  if (stopped || boundSessionId == null) return;
  if (reconnectTimer != null) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, backoffMs());
  reconnectAttempt += 1;
}

function clearTimers(): void {
  if (heartbeatTimer != null) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
  if (presenceTimer != null) {
    clearTimeout(presenceTimer);
    presenceTimer = null;
  }
}

function buildSubprotocols(): string[] {
  const ownerToken = getMapSpecSessionCursor().ownerToken;
  if (ownerToken) return ['session', ownerToken];
  const token = getAccessToken();
  if (token) return ['bearer', token];
  return [];
}

function startHeartbeat(socket: WebSocket): void {
  if (heartbeatTimer != null) clearInterval(heartbeatTimer);
  heartbeatTimer = setInterval(() => {
    if (socket.readyState !== WebSocket.OPEN) return;
    try {
      socket.send(JSON.stringify({ event: 'ping' }));
    } catch {
      /* 发送失败由 onclose 统一处理 */
    }
  }, HEARTBEAT_MS);
}

function connect(): void {
  if (stopped || boundSessionId == null) return;
  const protocols = buildSubprotocols();
  let socket: WebSocket;
  collabSetStatus(protocols.length > 0 ? 'connecting' : 'offline');
  if (protocols.length === 0) {
    // 无凭据：不发匿名连接（服务端必拒），诚实离线（单用户工作台完全可用）。
    scheduleReconnect();
    return;
  }
  try {
    socket = new WebSocket(`${WS_BASE}/api/v1/ws/collab/${boundSessionId}`, protocols);
  } catch (err) {
    devOnly.warn('[collab-client] connect failed:', err);
    scheduleReconnect();
    return;
  }
  ws = socket;

  socket.onopen = () => {
    reconnectAttempt = 0;
    collabSetStatus('online');
    startHeartbeat(socket);
    // R1-M2：连接即对账（knownRevision 不一致 → 服务端回放权威 doc）。
    sendNow({ event: 'sync', data: { knownRevision: lastKnownRevision } });
  };

  socket.onmessage = (msg: MessageEvent) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(String(msg.data));
    } catch {
      return;
    }
    if (parsed == null || typeof parsed !== 'object') return;
    const obj = parsed as Record<string, unknown>;
    if (obj.v === 1 && typeof obj.kind === 'string') {
      // 总线信封原生帧（ws_collab 直接 enqueue collab envelope）。
      handleInbound('bus', obj);
      return;
    }
    const event = typeof obj.event === 'string' ? obj.event : '';
    const data = typeof obj.data === 'object' && obj.data != null
      ? (obj.data as Record<string, unknown>)
      : {};
    handleInbound(event, data);
  };

  socket.onclose = () => {
    if (ws === socket) ws = null;
    clearTimers();
    collabSetStatus('connecting');
    scheduleReconnect();
  };
  socket.onerror = () => {
    try {
      socket.close();
    } catch {
      /* ignore */
    }
  };
}

function handleInbound(event: string, data: Record<string, unknown>): void {
  switch (event) {
    case 'hello': {
      const clientId = typeof data.clientId === 'string' ? data.clientId : null;
      collabSetSessionIdentity(clientId, data.degraded === true);
      if (Array.isArray(data.participants)) {
        const participants: CollabParticipant[] = data.participants
          .map(parseParticipant)
          .filter((p): p is CollabParticipant => p != null);
        collabSetParticipants(participants);
      }
      if (Array.isArray(data.leases)) {
        const leases: CollabLeaseInfo[] = data.leases
          .map(parseLeaseInfo)
          .filter((l): l is CollabLeaseInfo => l != null);
        collabSetLeases(leases);
      }
      if (typeof data.doc === 'object' && data.doc != null) {
        // 晚到/重连：hello 已带权威 doc（免 sync 往返）。
        handler?.('doc', data);
      } else {
        const revision = typeof data.revision === 'number' ? data.revision : null;
        if (revision != null && revision !== lastKnownRevision) void triggerRefetch();
      }
      break;
    }
    case 'pong': {
      const revision = typeof data.revision === 'number' ? data.revision : null;
      if (revision != null && revision !== lastKnownRevision) void triggerRefetch();
      break;
    }
    case 'sync_ok': {
      // 服务端确认无缺口；仍对齐 revision（可能与本地相同）。
      if (typeof data.revision === 'number') lastKnownRevision = Math.max(lastKnownRevision, data.revision);
      break;
    }
    case 'bus':
      handler?.('bus', data);
      break;
    default:
      handler?.(event, data);
  }
}

/** revision 对账 refetch：单飞 + 3s 最小间隔（防风暴）。 */
async function triggerRefetch(): Promise<void> {
  if (refetchInflight) return;
  const now = Date.now();
  if (now - lastRefetchAt < 3000) return;
  lastRefetchAt = now;
  refetchInflight = true;
  try {
    await (refetchFn?.() ?? Promise.resolve());
  } catch (err) {
    devOnly.warn('[collab-client] reconcile refetch failed:', err);
  } finally {
    refetchInflight = false;
  }
}

function sendNow(message: { event: string; data?: Record<string, unknown> }): boolean {
  if (ws == null || ws.readyState !== WebSocket.OPEN) return false;
  try {
    ws.send(JSON.stringify(message));
    return true;
  } catch {
    return false;
  }
}

/** 对外 presence 更新（coalesce 250ms；断线时丢弃 —— 瞬态可丢）。 */
export function sendCollabPresence(patchFields: Record<string, unknown>): void {
  pendingPresence = { ...(pendingPresence ?? {}), ...patchFields };
  if (presenceTimer != null) return;
  presenceTimer = setTimeout(() => {
    presenceTimer = null;
    const data = pendingPresence;
    pendingPresence = null;
    if (data != null && Object.keys(data).length > 0) sendNow({ event: 'presence', data });
  }, PRESENCE_COALESCE_MS);
}

/** lease 操作（结果经 inbound lease_result 事件由 adopt 处理）。 */
export function sendCollabLease(
  action: 'lease_acquire' | 'lease_renew' | 'lease_release',
  lockKey: string,
): void {
  sendNow({ event: action, data: { lockKey, requestId: `${Date.now()}` } });
}

/** 绑定会话（workspace 会话切换时调用；幂等）。 */
export function startCollabClient(
  sessionId: string,
  onEvent: InboundHandler,
): void {
  handler = onEvent;
  if (boundSessionId === sessionId && !stopped) return;
  stopCollabClient();
  stopped = false;
  boundSessionId = sessionId;
  collabResetForSession();
  collabSetStatus('connecting');
  connect();
}

export function stopCollabClient(): void {
  stopped = true;
  if (reconnectTimer != null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  clearTimers();
  if (ws != null) {
    const socket = ws;
    ws = null;
    socket.onclose = null;
    socket.onerror = null;
    socket.onmessage = null;
    socket.onopen = null;
    try {
      socket.close();
    } catch {
      /* ignore */
    }
  }
  boundSessionId = null;
  collabResetForSession();
}

/** 测试辅助。 */
export function collabClientSessionId(): string | null {
  return boundSessionId;
}
