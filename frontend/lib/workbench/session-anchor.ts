/**
 * 会话锚（Workbench V5 / W11）—— 刷新恢复的本地指针。
 *
 * 只存「指向后端会话的指针」（sessionId + 是否认证会话），绝不存工作台
 * 内容 —— 真相仍以后端 MapSpec/map-state 为唯一事实源（无影子 state store）。
 * 安全边界：仅认证会话自动恢复（JWT 本已持久化于 auth tokenStore）；匿名
 * 会话的 owner_token 是内存持有的会话能力，刷新即失效 —— 不为恢复便利
 * 把它写进 localStorage（不新增凭据持久化面）。
 */
import { getAccessToken } from '@/lib/auth/tokenStore';

const ANCHOR_KEY = 'wb5:session-anchor';

export interface SessionAnchor {
  sessionId: string;
  /** 锚创建时是否认证会话（匿名会话不自动恢复 —— 见模块头）。 */
  authed: boolean;
  savedAt: number;
}

export function readSessionAnchor(): SessionAnchor | null {
  try {
    const raw = window.localStorage.getItem(ANCHOR_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<SessionAnchor>;
    if (typeof parsed?.sessionId !== 'string' || parsed.sessionId.length === 0) return null;
    if (typeof parsed.savedAt !== 'number') return null;
    return {
      sessionId: parsed.sessionId,
      authed: parsed.authed === true,
      savedAt: parsed.savedAt,
    };
  } catch {
    return null;
  }
}

export function writeSessionAnchor(sessionId: string): void {
  try {
    const anchor: SessionAnchor = {
      sessionId,
      authed: getAccessToken() != null,
      savedAt: Date.now(),
    };
    window.localStorage.setItem(ANCHOR_KEY, JSON.stringify(anchor));
  } catch {
    // 存储不可用（隐私模式/配额）→ 恢复功能降级，功能主体不受影响
  }
}

export function clearSessionAnchor(): void {
  try {
    window.localStorage.removeItem(ANCHOR_KEY);
  } catch {
    // 同上：降级
  }
}

/**
 * 刷新恢复判定：认证会话 + 仍持有认证凭据 → 自动恢复；匿名会话或已登出
 * → 不恢复（返回 null 由调用方保持新会话语义）。
 */
export function restorableSessionAnchor(): SessionAnchor | null {
  const anchor = readSessionAnchor();
  if (!anchor) return null;
  if (!anchor.authed) return null;
  if (getAccessToken() == null) return null;
  return anchor;
}
