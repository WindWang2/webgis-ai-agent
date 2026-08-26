import { ApiError, apiFetch } from '@/lib/api/transport';
import {
  commitMapSpecDocument,
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  setMapSpecRevision,
  subscribeMapSpecLive,
} from '@/lib/mapspec/session-cursor';
import { devOnly } from '@/lib/utils/logger';
import type { ComponentPlacement } from '@/lib/mapspec-compiler/types';

/**
 * 组件局部突变提交（D3/D4）—— UI 拖拽/缩放/折叠/隐藏的唯一收尾通道。
 *
 * 与 user-mutation.ts 同款模式：apiFetch + getMapSpecSessionCursor 的
 * ownerToken + expected_revision CAS + setMapSpecRevision 收敛。差异在于
 * 409 superseded 语义：拖拽是高频轻交互，静默收敛（回灌服务端真相 +
 * devOnly warn），不走通用错误 toast —— 用户下一次手势会基于新 revision
 * 重试，无需打断。
 *
 * 乐观渲染（optimistic override store）：手势收尾先本地记录 placement，
 * 提交返回的 committed MapSpec 经 SSE/cursor 回流期间渲染器把 override
 * 叠在 spec 组件之上；spec 收敛到同一 placement（或会话切换）后自动清空。
 * 不进 useHudStore —— 图表状态与 HUD 无关，MapSpec 仍是唯一真相源。
 */

interface MutationResponse {
  success?: boolean;
  status?: string;
  mutation_revision?: number;
  mapspec?: { layout?: { components?: unknown[] } } & Record<string, unknown>;
  correction_hint?: string;
}

export interface ComponentPatch {
  enabled?: boolean;
  placement?: ComponentPlacement;
  variant?: string;
}

function supersededFromError(err: unknown): MutationResponse | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body as { detail?: MutationResponse } | MutationResponse | null;
  if (!body || typeof body !== 'object') return null;
  if ('detail' in body && body.detail && typeof body.detail === 'object') {
    return body.detail;
  }
  if ('status' in body && (body as MutationResponse).status === 'superseded') {
    return body as MutationResponse;
  }
  return null;
}

/**
 * 提交组件局部突变（POST mapspec/mutations intent=patch_component）。
 * 成功 → setMapSpecRevision + commitMapSpecDocument（chrome 立即重渲）；
 * 409 superseded → 同样回灌服务端真相后静默返回（拖拽不弹 toast）；
 * 其它错误 → 抛给调用方（FloatingChrome 回滚 override 并 devOnly.warn）。
 */
export async function commitComponentPatch(
  componentId: string,
  patch: ComponentPatch,
): Promise<void> {
  const { sessionId, revision, ownerToken } = getMapSpecSessionCursor();
  if (!sessionId) return;
  if (patch.enabled === undefined && patch.placement === undefined && patch.variant === undefined) {
    return;
  }
  try {
    const data = await apiFetch<MutationResponse>(
      `/api/v1/chat/sessions/${sessionId}/mapspec/mutations`,
      {
        method: 'POST',
        body: {
          intent: 'patch_component',
          expected_revision: revision,
          component_id: componentId,
          ...(patch.enabled !== undefined ? { enabled: patch.enabled } : {}),
          ...(patch.placement !== undefined ? { placement: patch.placement } : {}),
          ...(patch.variant !== undefined ? { variant: patch.variant } : {}),
        },
        ownerToken,
        label: 'MapSpec component patch mutation',
      },
    );
    if (typeof data.mutation_revision === 'number') {
      setMapSpecRevision(data.mutation_revision);
    }
    commitMapSpecDocument(data.mapspec);
  } catch (err) {
    const superseded = supersededFromError(err);
    if (!superseded) throw err;
    // 静默收敛：服务端已有更新真相 —— 回灌 revision + spec，拖拽交互不打断
    if (typeof superseded.mutation_revision === 'number') {
      setMapSpecRevision(superseded.mutation_revision);
    }
    commitMapSpecDocument(superseded.mapspec);
    // superseded 时的 override 与 server 真相不一致（如被并发 Agent 覆盖），
    // 则不应永久压住——清掉，让 spec 成为可见真相（review P2）。
    if (err instanceof ApiError && superseded.mapspec) {
      const spec = superseded.mapspec as { layout?: { components?: Array<{ id: string; placement?: unknown }> } };
      const serverPlacement = (spec.layout?.components || []).find((c) => c.id === componentId)?.placement;
      const override = getComponentPlacementOverride(componentId);
      if (override && JSON.stringify(serverPlacement) !== JSON.stringify(override)) {
        setComponentPlacementOverride(componentId, null);
      }
    }
    devOnly.warn('[component-mutation] patch superseded, converged to server truth');
  }
}

// ── 乐观 override store（tiny subscribe store，模式同 session-cursor）─────

const overrides = new Map<string, ComponentPlacement>();
let overrideGeneration = 0;
const listeners = new Set<() => void>();

function emitOverride(): void {
  overrideGeneration += 1;
  listeners.forEach((listener) => listener());
}

export function subscribeComponentOverrides(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getComponentOverridesGeneration(): number {
  return overrideGeneration;
}

export function getComponentPlacementOverride(componentId: string): ComponentPlacement | undefined {
  return overrides.get(componentId);
}

/** 记录/清除本地乐观 placement（null = 清除）。 */
export function setComponentPlacementOverride(
  componentId: string,
  placement: ComponentPlacement | null,
): void {
  if (!componentId) return;
  if (placement === null) {
    if (!overrides.has(componentId)) return;
    overrides.delete(componentId);
  } else {
    overrides.set(componentId, placement);
  }
  emitOverride();
}

function samePlacement(
  a: ComponentPlacement | undefined,
  b: ComponentPlacement | undefined,
): boolean {
  if (a == null || b == null) return a === b;
  return (
    a.mode === b.mode &&
    a.anchor === b.anchor &&
    a.x === b.x &&
    a.y === b.y &&
    a.width === b.width &&
    a.height === b.height &&
    a.zIndex === b.zIndex &&
    (a.collapsed ?? false) === (b.collapsed ?? false)
  );
}

/**
 * committed spec ↔ override 收敛：spec 已含同一 placement → 丢弃 override
 * （spec 是唯一真相）；spec 为空（会话切换 resetLiveState）→ 全清。
 * 订阅 mapspec live 代数，spec 每次回流时自动对账。
 */
function reconcileOverrides(): void {
  const spec = getCommittedMapSpec();
  if (spec == null) {
    if (overrides.size === 0) return;
    overrides.clear();
    emitOverride();
    return;
  }
  const byId = new Map(
    (spec.layout?.components ?? []).map((c) => [c.id, c.placement] as const),
  );
  let changed = false;
  for (const [componentId, placement] of overrides) {
    if (samePlacement(byId.get(componentId), placement)) {
      overrides.delete(componentId);
      changed = true;
    }
  }
  if (changed) emitOverride();
}

if (typeof window !== 'undefined') {
  subscribeMapSpecLive(() => reconcileOverrides());
}
