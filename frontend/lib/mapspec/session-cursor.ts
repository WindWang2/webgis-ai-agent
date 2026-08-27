/** Session cursor for user MapSpec Mutations (#639). No session → chrome stays local. */

import type { MapSpec } from '@/lib/mapspec-compiler/types';
import type { PendingPresentation } from '@/lib/mapspec/live-spec';
import { resetRefSourceCache } from '@/lib/mapspec/ref-source-resolver';

let sessionId: string | undefined;
let revision = 0;
let ownerToken: string | null = null;
let committed: MapSpec | null = null;
let pending: PendingPresentation = {};
let pendingRemoved: string[] = [];
let generation = 0;
const listeners = new Set<() => void>();

function curRevision(): number {
  return revision;
}

function emit(): void {
  generation += 1;
  listeners.forEach((listener) => listener());
}

export function resetLiveState(): void {
  committed = null;
  pending = {};
  pendingRemoved = [];
  // ref 数据缓存随会话失效（ref 归会话所有；切换后旧数据不可复用）。
  resetRefSourceCache();
}

export function subscribeMapSpecLive(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getMapSpecLiveGeneration(): number {
  return generation;
}

export function setMapSpecSessionCursor(
  nextId: string | undefined,
  nextRevision = 0,
  nextOwnerToken: string | null = null,
): void {
  sessionId = nextId;
  revision = Number.isFinite(nextRevision) ? nextRevision : 0;
  ownerToken = nextOwnerToken;
  resetLiveState();
  emit();
}

export function getMapSpecSessionCursor(): {
  sessionId: string | undefined;
  revision: number;
  ownerToken: string | null;
} {
  return { sessionId, revision, ownerToken };
}

export function setMapSpecRevision(nextRevision: number): void {
  // 单调保护（ST-P3-1）：HTTP 突变响应与 SSE agent 事件是两条信道，迟到的
  // 旧事件不得把游标 revision 拉回过去（否则下一次用户突变必然 spurious
  // 409）。restore 路径经 setMapSpecSessionCursor 整体重置，不受影响。
  if (!Number.isFinite(nextRevision)) return;
  if (nextRevision < revision) return;
  revision = nextRevision;
}

export function getCommittedMapSpec(): MapSpec | null {
  return committed;
}

export function commitMapSpecDocument(mapspec: unknown, revision?: number): void {
  if (!mapspec || typeof mapspec !== 'object') return;
  const spec = mapspec as MapSpec;
  if (!Array.isArray(spec.layers) && (spec.sources == null || typeof spec.sources !== 'object')) {
    return;
  }
  // 旧代次保护（ST-P3-1）：携带 revision 且低于游标当前值的 spec 是迟到
  // 信道上的旧真相——提交会让 committed 回退到旧代（下一次 compose 用旧
  // spec 组合）。无 revision（restore/未标注来源）按无条件提交。
  if (typeof revision === 'number' && Number.isFinite(revision) && revision < curRevision()) {
    return;
  }
  // #692：同一 spec 对象重复提交不 bump generation——此前无条件 emit，
  // map-panel 的 effect 重跑全量 compose + worker diff 只为得到空 patch；
  // 一轮多事件时按事件量放大。MapSpec 类型无身份字段，CoW 下重复提交
  // 常是同一对象引用（后端/桥不复制），对象同一性即足够的快路径；等值
  // 不同对象的深比较本身就是要省掉的成本，不做。
  if (spec === committed) return;
  committed = spec;
  emit();
}

export function getPendingPresentation(): PendingPresentation {
  return pending;
}

export function mergePendingPresentation(
  layerId: string,
  patch: { visible?: boolean; opacity?: number },
): void {
  if (!layerId) return;
  if (patch.visible === undefined && patch.opacity === undefined) return;
  pending = {
    ...pending,
    [layerId]: { ...pending[layerId], ...patch },
  };
  emit();
}

export function clearPendingPresentation(layerId?: string): void {
  if (!layerId) {
    if (Object.keys(pending).length === 0) return;
    pending = {};
  } else if (pending[layerId]) {
    const { [layerId]: _dropped, ...rest } = pending;
    pending = rest;
  } else {
    return;
  }
  emit();
}

export function getPendingRemoved(): string[] {
  return pendingRemoved;
}

export function markPendingRemoved(layerId: string): void {
  if (!layerId || pendingRemoved.includes(layerId)) return;
  pendingRemoved = [...pendingRemoved, layerId];
  emit();
}

export function clearPendingRemoved(layerId?: string): void {
  if (!layerId) {
    if (pendingRemoved.length === 0) return;
    pendingRemoved = [];
  } else if (pendingRemoved.includes(layerId)) {
    pendingRemoved = pendingRemoved.filter((id) => id !== layerId);
  } else {
    return;
  }
  emit();
}
