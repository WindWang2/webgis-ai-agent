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
import {
  commitMapSpecDocument,
  getMapSpecSessionCursor,
  setMapSpecRevision,
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
  return enqueueUserMutation(async (): Promise<TemporalCommitOutcome> => {
    const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId || sessionId !== enqueuedSessionId) {
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
      if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) {
        return { outcome: 'failed', error: new Error('session detached') };
      }
      if (typeof data.mutation_revision === 'number') {
        setMapSpecRevision(data.mutation_revision);
      }
      if (data.mapspec) {
        commitMapSpecDocument(data.mapspec, data.mutation_revision);
      }
      return { outcome: 'committed', revision: data.mutation_revision };
    } catch (err) {
      if (err && typeof err === 'object' && 'status' in err
        && (err as { status?: number }).status === 409) {
        return { outcome: 'superseded' };
      }
      return { outcome: 'failed', error: err instanceof Error ? err : new Error(String(err)) };
    }
  });
}
