/**
 * G3D05（ADR-0275 D3）：用户场景写通道。
 *
 * 场景控制器（mode/夸张/pitch/bearing）与 Agent 的 set_map_scene 工具
 * 汇入**同一**服务端 mutation 端点与 SetSceneIntent 事务管线 ——
 * 人机共享同一真相，无第二条状态通道。串行链/幂等键/409 superseded
 * 语义与 user-mutation.ts 既有纪律一致（复用 enqueueUserMutation）。
 */
import { ApiError, apiFetch } from '@/lib/api/transport';
import {
  commitMapSpecDocument,
  getMapSpecSessionCursor,
  setMapSpecRevision,
} from '@/lib/mapspec/session-cursor';
import { enqueueUserMutation, newMutationId } from '@/lib/mapspec/user-mutation';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

export interface UserScenePayload {
  mode?: '2d' | '2.5d' | '3d';
  terrain?: {
    source?: string;
    exaggeration?: number;
    vertical_unit?: string;
    elevation_ref?: string;
  } | null;
  camera?: {
    pitch?: number;
    bearing?: number;
    transition_ms?: number;
  };
  reason_code?: string;
}

interface MutationResponse {
  success?: boolean;
  status?: string;
  mutation_revision?: number;
  mapspec?: MapSpec;
  correction_hint?: string;
}

export type SceneCommitOutcome =
  | { outcome: 'committed'; revision?: number }
  | { outcome: 'superseded'; revision?: number }
  | { outcome: 'failed'; error: unknown };

/**
 * 提交用户场景变更；`scene = null` 清除场景回平面。
 *
 * 返回落定结局（不抛出 —— 调用方按结局回滚 UI 乐观态/提示）。
 * 响应的完整 mapspec 经 commitMapSpecDocument 进入 committed 真相
 * （含 scene 块 —— map-panel 的 sceneCameraIdentity/terrain 效果随之
 * 生效），失败/superseded 不写 committed（服务端真相未含本笔）。
 */
export async function commitUserScene(
  scene: UserScenePayload | null,
): Promise<SceneCommitOutcome> {
  const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
  const mutationId = newMutationId();
  return enqueueUserMutation(async (): Promise<SceneCommitOutcome> => {
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
            intent: 'set_scene',
            expected_revision: revision,
            scene,
            client_mutation_id: mutationId,
          },
          ownerToken,
          timeoutMs: 60_000,
          label: 'MapSpec scene mutation',
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
      // 409 superseded：服务端真相回灌（含权威 revision），用户操作被
      // 更新状态取代 —— 不重放（与 commitLayerPresentation 同纪律；
      // detail 信封形状解析与 supersededFromError 同款）。
      if (err instanceof ApiError && err.status === 409) {
        const raw = err.body as { detail?: MutationResponse } | MutationResponse | null;
        const body = (
          raw && typeof raw === 'object' && 'detail' in raw
            ? raw.detail
            : raw
        ) as MutationResponse | undefined;
        if (getMapSpecSessionCursor().sessionId !== enqueuedSessionId) {
          return { outcome: 'failed', error: err };
        }
        if (typeof body?.mutation_revision === 'number') {
          setMapSpecRevision(body.mutation_revision);
        }
        if (body?.mapspec) {
          commitMapSpecDocument(body.mapspec, body.mutation_revision);
        }
        return { outcome: 'superseded', revision: body?.mutation_revision };
      }
      return { outcome: 'failed', error: err };
    }
  });
}
