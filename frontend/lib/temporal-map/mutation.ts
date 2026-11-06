/**
 * temporal-map 用户面 CAS 提交（GOAL 03 M4）。
 *
 * 用户时间轴操作（seek/pause/rate/unpin）→ POST mapspec/mutations
 * intent=set_temporal_view（origin=user，expected_revision CAS）—— 与
 * commitUserScene 同款队列/幂等/会话守卫语义；响应 spec 经
 * commitMapSpecDocument 进入 committed 真相（时间游标单一真相 = spec）。
 * Agent 面控制不经过本模块（工具 → 服务端 → SSE work echo → spec bump）。
 */
import { apiFetch } from '@/lib/api/transport';
import { emitTemporalWindowSettled } from '@/lib/interaction/bus';
import {
  commitMapSpecDocument,
  getMapSpecSessionCursor,
  setMapSpecRevision,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { enqueueUserMutation, newMutationId } from '@/lib/mapspec/user-mutation';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

export interface TemporalPatch {
  cursor_frame?: string;
  playback?: { playing?: boolean; rate?: number };
  compare?: { enabled: boolean; frame_id_b: string; classification?: unknown } | null;
  user_pinned?: boolean;
}

interface MutationResponse {
  success?: boolean;
  status?: string;
  mutation_revision?: number;
  mapspec?: MapSpec;
  correction_hint?: string;
}

export type TemporalCommitOutcome =
  | { outcome: 'committed'; revision: number | undefined }
  | { outcome: 'superseded' }
  | { outcome: 'failed'; error: Error };

/**
 * 用户面 temporal patch 提交（queued + CAS + 幂等 client_mutation_id）。
 * 不抛 —— 调用方按 outcome 决定 UI 反馈（superseded = 并发让位，不重试）。
 */
export function commitUserTemporal(patch: TemporalPatch): Promise<TemporalCommitOutcome> {
  const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
  const mutationId = newMutationId();
  // Remember a detach even if A→B→A finishes before this request settles.
  let detached = !enqueuedSessionId;
  const unsubscribe = subscribeMapSpecLive(() => {
    if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) detached = true;
  });
  const isAttached = () => !detached
    && getMapSpecSessionCursor().sessionId === enqueuedSessionId;
  return enqueueUserMutation(async (): Promise<TemporalCommitOutcome> => {
    const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId || !isAttached()) {
      return { outcome: 'failed', error: new Error('session detached') };
    }
    try {
      const data = await apiFetch<MutationResponse>(
        `/api/v1/chat/sessions/${sessionId}/mapspec/mutations`,
        {
          method: 'POST',
          body: {
            intent: 'set_temporal_view',
            expected_revision: revision,
            patch,
            client_mutation_id: mutationId,
          },
          ownerToken,
          timeoutMs: 30_000,
          label: 'MapSpec temporal mutation',
        },
      );
      if (!isAttached()) {
        return { outcome: 'failed', error: new Error('session detached') };
      }
      if (typeof data.mutation_revision === 'number') {
        setMapSpecRevision(data.mutation_revision);
      }
      if (data.mapspec) {
        commitMapSpecDocument(data.mapspec, data.mutation_revision);
      }
      emitTemporalIr(patch, mutationId, 'committed', data.mutation_revision);
      return { outcome: 'committed', revision: data.mutation_revision };
    } catch (err) {
      if (!isAttached()) {
        return { outcome: 'failed', error: new Error('session detached') };
      }
      if (err && typeof err === 'object' && 'status' in err
        && (err as { status?: number }).status === 409) {
        emitTemporalIr(patch, mutationId, 'superseded');
        return { outcome: 'superseded' };
      }
      emitTemporalIr(patch, mutationId, 'failed');
      return { outcome: 'failed', error: err instanceof Error ? err : new Error(String(err)) };
    }
  }).finally(unsubscribe);
}

/**
 * temporal settle 的 IR 上行（ADR-0283 D2）：只报已知事实——游标帧/播放/
 * 锁定；不虚构 mode/field/extent（spec time 面的字段不在 patch 上下文里）。
 * 观察层事件绝不阻塞提交链路（emit 内部已 try/catch 静默）。
 */
function emitTemporalIr(
  patch: TemporalPatch,
  mutationId: string,
  outcome: 'committed' | 'superseded' | 'failed',
  mutationRevision?: number,
): void {
  try {
    emitTemporalWindowSettled(
      {
        ...(patch.cursor_frame !== undefined ? { current: patch.cursor_frame } : {}),
        ...(patch.playback?.playing !== undefined
          ? { mode: patch.playback.playing ? 'playback' : 'pause' }
          : {}),
        ...(patch.user_pinned !== undefined ? { pinned: patch.user_pinned } : {}),
      },
      {
        clientMutationId: mutationId,
        outcome,
        mutationRevision,
      },
    );
  } catch {
    // 观察层绝不阻塞 mutation 链路。
  }
}
