/**
 * G25 编辑范围路由（P2/P5）—— 活动画幅在场时，把「允许 override」的
 * 图层操作路由到该帧 layerOverrides（layout.frames[].layerOverrides），
 * 不修改全部 frames 或根图层默认值。
 *
 * 边界（诚实披露）：
 * - Frame 契约允许的 override 词表 = {visible, opacity}（LayerOverride）；
 *   其它属性（paint 颜色等）是产品根级 —— 不路由、不静默扩权；
 * - 写入走既有 set_layout intent（frames 整表携带 + expected_revision CAS
 *   + 409 superseded 回灌 —— G15 同门），不新建 intent；
 * - 「只把第二幅 X 改掉，不动其余」由 stable frame_id 定位：目标帧的
 *   layerOverrides 目标层合并写入，其余帧逐字携带。
 */
import { apiFetch, ApiError } from '@/lib/api/transport';
import {
  commitMapSpecDocument,
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  setMapSpecRevision,
} from '@/lib/mapspec/session-cursor';
import { enqueueUserMutation } from '@/lib/mapspec/user-mutation';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

export type FrameEditScope = 'frame_only' | 'product';

export interface FrameOverridePatch {
  layerId: string;
  override: { opacity?: number; visible?: boolean };
}

interface MutationResponse {
  success?: boolean;
  status?: string;
  mutation_revision?: number;
  mapspec?: Record<string, unknown>;
}

/** scope 判定（UI 徽标与路由共用同一纯函数 —— 不出现两套真相）。 */
export function resolveEditScope(gridOpen: boolean, activeFrameId: string | null): FrameEditScope {
  return gridOpen && activeFrameId ? 'frame_only' : 'product';
}

/** scope 用户面命名（P5：操作名称明确「仅本幅/同组/整个产品」）。 */
export function frameEditScopeLabel(scope: FrameEditScope): string {
  return scope === 'frame_only' ? '仅本幅' : '整个产品';
}

/**
 * 把 override 写入目标帧 layerOverrides（frames 整表携带，目标帧外的槽
 * 逐字保留 —— A03「仅改第二幅」的构造性保证）。
 * 返回 'routed' = 已路由到帧；'no_active_frame' = 无活动画幅（调用方走
 * 根级默认路径）。
 */
export async function commitLayerOverrideForFrame(
  frameId: string,
  patch: FrameOverridePatch,
): Promise<'routed' | 'superseded'> {
  const enqueuedSessionId = getMapSpecSessionCursor().sessionId;
  let outcome: 'routed' | 'superseded' = 'routed';
  await enqueueUserMutation(async () => {
    const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId || sessionId !== enqueuedSessionId) return;
    const spec = (await readCurrentSpec(sessionId, ownerToken)) as MapSpec | null;
    if (!spec) return;
    const frames = Array.isArray(spec.layout?.frames) ? [...spec.layout!.frames!] : [];
    if (!frames.some((f) => f && f.id === frameId)) return; // 帧缺席 → 不写
    const nextFrames = frames.map((f) => {
      if (!f || f.id !== frameId) return f; // 其余帧逐字携带（不动其余）
      const overrides = { ...(f.layerOverrides ?? {}) };
      const prev = overrides[patch.layerId] ?? {};
      overrides[patch.layerId] = { ...prev, ...patch.override };
      return { ...f, layerOverrides: overrides };
    });
    try {
      const data = await apiFetch<MutationResponse>(
        `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/mapspec/mutations`,
        {
          method: 'POST',
          body: {
            intent: 'set_layout',
            expected_revision: revision,
            frames: nextFrames,
          },
          ownerToken,
          timeoutMs: 60_000,
          label: 'G25 frame layer override',
        },
      );
      if (typeof data.mutation_revision === 'number') {
        setMapSpecRevision(data.mutation_revision);
      }
      if (data.mapspec) {
        commitMapSpecDocument(data.mapspec as never, data.mutation_revision ?? revision);
      }
      outcome = 'routed';
    } catch (err) {
      const superseded = supersededBody(err);
      if (superseded) {
        outcome = 'superseded';
        if (typeof superseded.mutation_revision === 'number') {
          setMapSpecRevision(superseded.mutation_revision);
        }
        if (superseded.mapspec) {
          commitMapSpecDocument(superseded.mapspec as never, superseded.mutation_revision ?? revision);
        }
        return; // 静默收敛（G15 语义）
      }
      throw err;
    }
  });
  return outcome;
}

function supersededBody(err: unknown): MutationResponse | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body as { detail?: MutationResponse } | MutationResponse | null;
  if (body && typeof body === 'object' && 'detail' in body && body.detail) return body.detail;
  return (body as MutationResponse) ?? null;
}

async function readCurrentSpec(sessionId: string, ownerToken: string | null): Promise<Record<string, unknown> | null> {
  // 优先本地 committed（session-cursor 已回灌的最新真相）；缺失才读服务端。
  const local = getCommittedMapSpec();
  if (local) return local as unknown as Record<string, unknown>;
  const data = await apiFetch<{ mapspec?: Record<string, unknown> }>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/mapspec`,
    { ownerToken, timeoutMs: 30_000, label: 'G25 read mapspec' },
  );
  return data.mapspec ?? null;
}
