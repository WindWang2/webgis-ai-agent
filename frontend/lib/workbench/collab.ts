/**
 * Workbench 多 tab 协同基座（Workbench V5 / W5）。
 *
 * 同一会话的多 tab 通过 BroadcastChannel（`wb5:{sessionId}`，同源同浏览器）
 * 广播「已提交」的组织态事实 —— 接收方水合远端 doc 并对齐 revision 基线。
 * 确定性冲突解决 = 既有服务端 CAS last-writer-wins（本模块不裁决冲突，
 * 只传播已提交真相，绝不发明第二事实源）：
 *   - 收到 revision 更新或相等的 doc → 水合（远端是服务器已提交真相）；
 *   - 收到更旧 revision → 忽略（本地已见更新的服务器状态）。
 * 权限边界：channel 名含 sessionId，消息同源同浏览器；服务端所有权校验
 * （ownerToken / session 归属）不变 —— 跨用户/跨浏览器无此通道。
 * 环境降级：无 BroadcastChannel（旧浏览器 / 测试环境）时全部为 no-op。
 */
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { normalizeWorkbenchDoc, type WorkbenchDocV5 } from './doc';
import { devOnly } from '@/lib/utils/logger';

const CHANNEL_PREFIX = 'wb5:';

export interface WbCollabDocMessage {
  kind: 'doc';
  from: string;
  sessionId: string;
  revision: number;
  doc: WorkbenchDocV5;
}

export interface WbCollabHelloMessage {
  kind: 'hello';
  from: string;
  sessionId: string;
}

export type WbCollabMessage = WbCollabDocMessage | WbCollabHelloMessage;

let channel: BroadcastChannel | null = null;
let boundSessionId: string | null = null;
const tabId = `tab-${Math.random().toString(36).slice(2, 10)}`;

/** 接收远端 doc 时的落地回调（persistence 注册：水合 + 基线对齐 + 防回声）。 */
let adoptDoc: ((doc: WorkbenchDocV5, revision: number) => void) | null = null;

/** 回应 hello 的取 doc 回调（persistence 注册：返回当前已提交 doc）。 */
let currentDoc: (() => { doc: WorkbenchDocV5; revision: number } | null) | null = null;

export function bindCollabAdapters(
  adapters: {
    adoptDoc: (doc: WorkbenchDocV5, revision: number) => void;
    currentDoc: () => { doc: WorkbenchDocV5; revision: number } | null;
  },
): void {
  adoptDoc = adapters.adoptDoc;
  currentDoc = adapters.currentDoc;
}

function channelName(sessionId: string): string {
  return `${CHANNEL_PREFIX}${sessionId}`;
}

function post(message: WbCollabMessage): void {
  try {
    channel?.postMessage(message);
  } catch (err) {
    devOnly.warn('[wb-collab] postMessage failed:', err);
  }
}

/**
 * 启动协同通道（workspace 挂载一次；幂等）。无 BroadcastChannel 环境为
 * no-op（单 tab 场景完整可用 —— 协同是增强不是依赖）。
 */
export function startWorkbenchCollab(): void {
  if (channel != null) return;
  if (typeof BroadcastChannel === 'undefined') return;
  if (boundSessionId == null) return;
  openChannel(boundSessionId);
}

function openChannel(sessionId: string): void {
  channel?.close();
  channel = new BroadcastChannel(channelName(sessionId));
  channel.onmessage = (event: MessageEvent) => {
    const msg = event.data as WbCollabMessage | null;
    if (!msg || typeof msg !== 'object') return;
    if (msg.sessionId !== boundSessionId) return; // 权限边界：仅本会话消息
    if (msg.from === tabId) return; // 自回声
    if (msg.kind === 'hello') {
      // 晚到 tab 请求当前 doc：只回答已武装（有基线）的 tab。
      const snapshot = currentDoc?.() ?? null;
      if (snapshot) {
        post({
          kind: 'doc',
          from: tabId,
          sessionId: boundSessionId!,
          revision: snapshot.revision,
          doc: snapshot.doc,
        });
      }
      return;
    }
    if (msg.kind === 'doc') {
      const doc = normalizeWorkbenchDoc(msg.doc);
      if (!doc) return;
      const localRevision = getMapSpecSessionCursor().revision;
      // 确定性：仅接受「不旧于本地已见服务器状态」的 doc；更旧 = 已被
      // 服务器 CAS 裁决淘汰的写者，忽略。
      if (Number.isFinite(localRevision) && msg.revision < localRevision) return;
      adoptDoc?.(doc, msg.revision);
    }
  };
}

/** 会话切换：换通道 + 广播 hello 请求当前 doc（晚到 tab 收敛）。 */
export function collabSessionChanged(sessionId: string | null): void {
  boundSessionId = sessionId;
  if (sessionId == null) {
    channel?.close();
    channel = null;
    return;
  }
  if (typeof BroadcastChannel === 'undefined') return;
  startWorkbenchCollab();
  post({ kind: 'hello', from: tabId, sessionId });
}

/** doc 提交成功后广播（persistence 调用）。 */
export function collabBroadcastDoc(doc: WorkbenchDocV5, revision: number): void {
  if (boundSessionId == null) return;
  post({ kind: 'doc', from: tabId, sessionId: boundSessionId, revision, doc });
}

/** 测试隔离。 */
export function stopWorkbenchCollab(): void {
  channel?.close();
  channel = null;
  boundSessionId = null;
}

/** 测试辅助。 */
export function collabTabId(): string {
  return tabId;
}
