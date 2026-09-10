/**
 * Collab 事件协议（Workbench V6）—— 与后端 ws_collab / collab/bus 对齐的
 * 类型与解析。信任边界：一切入站数据经此归一化/裁剪后才进 store；
 * 凭据绝不入事件载荷。
 */

export type CollabEventKind =
  | 'doc'
  | 'delta'
  | 'presentation'
  | 'presence'
  | 'lock'
  | 'op'
  | 'artifact';

/** 总线信封（mutation 派生事件 seq = mutation_revision；瞬态事件 seq = 服务端 time_ns）。 */
export interface CollabEnvelope {
  v: 1;
  kind: CollabEventKind;
  sid: string;
  seq: number;
  ts: string;
  data: Record<string, unknown>;
  truncated?: boolean;
}

/** WS 入站消息（{event, data} 信封，与既有 /ws 感知通道一致）。 */
export interface WsInbound {
  event: 'hello' | 'pong' | 'sync_ok' | 'doc' | 'presence_full' | 'lease_result'
  | 'bus';
  data?: Record<string, unknown>;
}

export interface CollabParticipant {
  clientId: string;
  label?: string;
  kind?: string;
  color?: string;
  viewport?: Record<string, unknown>;
  selectionIds?: string[];
  editingLayerId?: string;
  editingGroupId?: string;
}

export interface CollabLeaseInfo {
  lockKey: string;
  client: string;
  label?: string;
}

export interface CollabOpEntry {
  id: string;
  seq: number;
  actor: string;
  origin: string;
  kind: string;
  target?: string;
  label: string;
  summary?: string;
  ts: number;
}

export const COLLAB_MAX_PARTICIPANTS = 32;
export const COLLAB_MAX_REMOTE_OPS = 200;
export const COLLAB_MAX_SELECTION_IDS = 50;

function asFiniteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function clampString(value: unknown, max: number): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value.slice(0, max) : undefined;
}

/** 入站 envelope 归一化（非法 → null 丢弃）。 */
export function parseEnvelope(raw: unknown): CollabEnvelope | null {
  if (raw == null || typeof raw !== 'object') return null;
  const candidate = raw as Partial<CollabEnvelope>;
  if (candidate.v !== 1) return null;
  const kind = candidate.kind;
  if (kind !== 'doc' && kind !== 'delta' && kind !== 'presentation' && kind !== 'presence'
    && kind !== 'lock' && kind !== 'op' && kind !== 'artifact') return null;
  const sid = clampString(candidate.sid, 200);
  const seq = asFiniteNumber(candidate.seq);
  if (sid == null || seq == null) return null;
  return {
    v: 1,
    kind,
    sid,
    seq,
    ts: clampString(candidate.ts, 40) ?? '',
    data: typeof candidate.data === 'object' && candidate.data != null ? candidate.data : {},
    truncated: candidate.truncated === true,
  };
}

/** presence 记录裁剪（隐私最小集 + 长度上限；与后端白名单同向）。 */
export function parseParticipant(raw: unknown): CollabParticipant | null {
  if (raw == null || typeof raw !== 'object') return null;
  const c = raw as Record<string, unknown>;
  const clientId = clampString(c.clientId ?? c.cid, 64);
  if (!clientId) return null;
  const selectionIds = Array.isArray(c.selectionIds)
    ? c.selectionIds
      .filter((x): x is string => typeof x === 'string')
      .slice(0, COLLAB_MAX_SELECTION_IDS)
    : undefined;
  const viewport = typeof c.viewport === 'object' && c.viewport != null
    ? (c.viewport as Record<string, unknown>)
    : undefined;
  return {
    clientId,
    label: clampString(c.label, 120),
    kind: clampString(c.kind, 16),
    color: clampString(c.color, 32),
    viewport,
    selectionIds,
    editingLayerId: clampString(c.editingLayerId, 200),
    editingGroupId: clampString(c.editingGroupId, 200),
  };
}

export function parseLeaseInfo(raw: unknown): CollabLeaseInfo | null {
  if (raw == null || typeof raw !== 'object') return null;
  const c = raw as Record<string, unknown>;
  const lockKey = clampString(c.lockKey, 260);
  const client = clampString(c.client, 120);
  if (!lockKey || !client) return null;
  return { lockKey, client, label: clampString(c.label, 120) };
}

/** 入站 op 事件 → journal 条目。 */
export function parseOpEntry(envelope: CollabEnvelope, seqId: number): CollabOpEntry | null {
  const kind = clampString(envelope.data.kind, 60) ?? 'mutation';
  const label = clampString(envelope.data.label, 80) ?? kind;
  return {
    id: `rop-${seqId}`,
    seq: envelope.seq,
    actor: clampString(envelope.data.actor, 60) ?? 'unknown',
    origin: clampString(envelope.data.origin, 16) ?? 'agent',
    kind,
    target: clampString(envelope.data.target, 200),
    label,
    summary: clampString(envelope.data.summary, 120),
    ts: Date.parse(envelope.ts) || Date.now(),
  };
}

/** 参与者展示色（按 clientId 确定性取色；无凭据语义）。 */
const PARTICIPANT_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#6366f1',
] as const;

export function participantColor(clientId: string): string {
  let hash = 0;
  for (let i = 0; i < clientId.length; i += 1) hash = (hash * 31 + clientId.charCodeAt(i)) | 0;
  return PARTICIPANT_COLORS[Math.abs(hash) % PARTICIPANT_COLORS.length];
}
