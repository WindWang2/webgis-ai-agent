/**
 * Unified Map Runtime Layer Registry —— 命令式运行时图层的唯一挂载账本
 * （GIS Runtime v3, Phase B, ADR-0080）。
 *
 * Runtime v2 post-merge 审计（A4）：命令式 `custom-*` 覆盖层存在**两套**
 * 互相不知道的账本——
 *
 *   lib/map-kit/custom-overlay-registry.ts      （source/layer 定义重放；
 *                                                生产 remount 路径）
 *   lib/map-commands/custom-overlay-registry.ts （重挂闭包 LRU；其
 *                                                remountCustomOverlays
 *                                                                无生产调用方）
 *
 * 双事实源的后果：closure 账本被 remember/forget 维护却从不重放（死重），
 * 定义账本不设界（无 LRU），且 `addNativeHeatmap` 不经 recordCustomOverlayLayer
 * 缝——native heatmap 的 layer 定义从未入账，basemap 切换后 source 重挂而
 * **图层永久消失**。
 *
 * 本模块是唯一的 canonical 存储。两条旧路径都改为 facade（同 API 委托），
 * adapter ≠ second storage —— 所有写入落到这里，重放只走这里：
 *
 *   registerRuntimeLayer / recordRuntimeSource / recordRuntimeLayer
 *   unregisterRuntimeLayer（层族前缀清扫，含其独占 source）
 *   clearRuntimeLayerRegistry（会话切换）
 *   remountRuntimeLayers（style reload 后按插入序重放：先 sources 后
 *                         layers；定义优先，闭包兜底）
 *
 * 描述符字段按当前真实消费裁剪：
 *  - identity：runtimeLayerId / sourceId（MapLibre 层与源的权威 id）；
 *  - replay：sourceDef / layerDef / remount（闭包兜底——覆盖不经 renderer
 *    缝的挂载路径）；
 *  - family：vector | raster | heatmap | annotation | custom（观测与重放
 *    断言用，从 MapLibre layer type 派生）；
 *  - ownership / mountMode / persistence：当前全部条目恒为
 *    command / imperative / session —— spec 层的挂载真相在 MapSpec +
 *    MapSpecRuntime.reconcile（declarative），不进本账本（避免第二份
 *    spec 真相）；字段保留为显式契约边界；
 *  - zGroup / seq：custom 带内目前单一置顶带（zGroup=0），seq 为插入
 *    世代（重放序 = 插入序）。
 *
 * 有界：LRU 256 条（Map 插入序驱逐最旧），防长会话无限增长。
 */

import { devOnly } from '@/lib/utils/logger';

export type RuntimeLayerFamily = 'vector' | 'raster' | 'heatmap' | 'annotation' | 'custom';
export type RuntimeLayerOwnership = 'spec' | 'command' | 'harness' | 'user' | 'system';
export type RuntimeLayerPersistence = 'durable' | 'session' | 'transient';
export type RuntimeLayerMountMode = 'declarative' | 'imperative';

export interface RuntimeLayerSourceDef {
  kind: 'geojson' | 'image';
  data?: any;
  url?: string;
  coordinates?: [[number, number], [number, number], [number, number], [number, number]];
}

export interface RuntimeLayerDef {
  id: string;
  type: string;
  source: string;
  paint?: Record<string, unknown>;
  layout?: Record<string, unknown>;
  filter?: unknown;
  beforeId?: string;
}

export interface RuntimeLayerDescriptor {
  /** MapLibre layer id（独立 source 无层时暂为 source id）。 */
  runtimeLayerId: string;
  /** 该层引用的 MapLibre source id（可与 runtimeLayerId 相同）。 */
  sourceId: string;
  family: RuntimeLayerFamily;
  ownership: RuntimeLayerOwnership;
  persistence: RuntimeLayerPersistence;
  mountMode: RuntimeLayerMountMode;
  /** custom 带内 Z 组（当前单一置顶带 0）。 */
  zGroup: number;
  /** 插入世代（重放序 = 插入序）。 */
  seq: number;
  /** LRU 触碰时间戳（驱逐序；与插入序分离 —— upsert 不扰动重放序）。 */
  lastTouched: number;
  sourceDef?: RuntimeLayerSourceDef;
  layerDef?: RuntimeLayerDef;
  /** 闭包兜底重挂（命令路径登记；定义重放缺失时使用）。 */
  remount?: (map: any) => void;
}

const MAX_RUNTIME_LAYERS = 256;
/** 驱逐披露环（有界）：最近被驱逐的层 id —— reconcile/诊断可观测。 */
const MAX_EVICTED_LOG = 64;

/** runtimeLayerId → descriptor（Map 保持插入序 = 重放序）。 */
const registry = new Map<string, RuntimeLayerDescriptor>();
/** sourceId → 账目键（O(1) 吸附查找；挂载 N 层不产生 O(N²) 扫描）。 */
const sourceIndex = new Map<string, string>();
/** 最近驱逐环（id 顺序，最旧在前）。 */
const evictedLog: string[] = [];
let generation = 0;
let touchClock = 0;

function _touch(): number {
  touchClock += 1;
  return touchClock;
}

function familyForLayerType(type: string | undefined): RuntimeLayerFamily {
  switch (type) {
    case 'heatmap': return 'heatmap';
    case 'raster': return 'raster';
    case 'fill':
    case 'line':
    case 'circle':
    case 'symbol':
    case 'fill-extrusion': return 'vector';
    default: return 'custom';
  }
}

function findEntryBySource(sourceId: string): RuntimeLayerDescriptor | undefined {
  const key = sourceIndex.get(sourceId);
  if (!key) return undefined;
  const entry = registry.get(key);
  if (entry && entry.sourceId === sourceId) return entry;
  sourceIndex.delete(sourceId);
  return undefined;
}

function evictToBound(): void {
  let evicted = 0;
  while (registry.size > MAX_RUNTIME_LAYERS) {
    // P5（ADR-0080 lifecycle）：按 lastTouched 驱逐（真 LRU）—— 插入序
    // 是重放序（z 序稳定），驱逐序与之分离：最近未触碰的条目先走，
    // 频繁 upsert/可见性变更的层不再被误逐（此前实现是 FIFO，upsert
    // 不刷新位次）。
    let oldestKey: string | undefined;
    let oldestTouch = Infinity;
    let oldestSeq = Infinity;
    for (const [key, entry] of registry) {
      if (
        entry.lastTouched < oldestTouch ||
        (entry.lastTouched === oldestTouch && entry.seq < oldestSeq)
      ) {
        oldestKey = key;
        oldestTouch = entry.lastTouched;
        oldestSeq = entry.seq;
      }
    }
    if (oldestKey === undefined) break;
    const entry = registry.get(oldestKey);
    const snapshot = entry ? { ...entry, sourceDef: entry.sourceDef } : undefined;
    dropEntry(oldestKey);
    evictedLog.push(oldestKey);
    if (evictedLog.length > MAX_EVICTED_LOG) evictedLog.shift();
    // LRU 驱逐与反注册共用 source 所有权转移（review P2：两条路径一个
    // 标准 —— 驱逐持有 sourceDef 的账目时不剥夺共享层的重放能力）。
    if (snapshot) _transferSourceOwnership(snapshot);
    evicted += 1;
  }
  if (evicted > 0) {
    // review-C P2：驱逐是覆盖性回归（被驱逐层的 style-reload 重放静默
    // 消失）——devOnly 披露 + evictedLog 可观测（长会话超界可诊断）。
    devOnly.warn(
      `[runtime-layer-registry] ${evicted} least-recently-touched overlay(s) evicted at cap ` +
      `${MAX_RUNTIME_LAYERS} — they will no longer remount after a style reload`,
    );
  }
}

function dropEntry(key: string): void {
  const entry = registry.get(key);
  if (!entry) return;
  if (sourceIndex.get(entry.sourceId) === key) sourceIndex.delete(entry.sourceId);
  registry.delete(key);
}

/**
 * 登记/合并一条运行时图层（upsert：同 id 刷新定义与闭包，保持首挂插入
 * 位次）。查找顺序：层 id 键 → source id 吸附——**仅吸附还没有 layerDef
 * 的 source 记账**（review-A/B/C：source 先行建账、层随后到达时账目升级
 * 为层键）；已持有别的层定义的条目绝不被覆盖或删除——一个 source 可以
 * 服务多个层（如 fill+outline 对），吸附会静默丢掉第一个层的重放定义。
 * 家族前缀（`id-*` / `id__*`）属于同一命令栈，由 unregisterRuntimeLayer
 * 统一清扫——这里不做前缀推断。
 */
export function registerRuntimeLayer(input: {
  id: string;
  sourceId?: string;
  sourceDef?: RuntimeLayerSourceDef;
  layerDef?: RuntimeLayerDef;
  remount?: (map: any) => void;
  family?: RuntimeLayerFamily;
}): void {
  if (!input?.id) return;
  let existing = registry.get(input.id);
  if (!existing && input.sourceId) {
    const candidate = findEntryBySource(input.sourceId);
    if (candidate && !candidate.layerDef) existing = candidate;
  }
  if (!existing && input.layerDef) {
    // 闭包登记（rememberRuntimeRemount）不带 sourceId —— 按 id 或其
    // source 记账吸附（add_raster_layer 闭包键 = 层 id），同样只吸附
    // 无层定义的记账。
    const candidate = findEntryBySource(input.id);
    if (candidate && !candidate.layerDef) existing = candidate;
  }
  if (existing && existing.runtimeLayerId !== input.id) {
    // source 记账升级为层键：迁移到层 id（插入位次自然刷新）。
    dropEntry(existing.runtimeLayerId);
  }
  const layerType = input.layerDef?.type ?? existing?.layerDef?.type;
  const descriptor: RuntimeLayerDescriptor = {
    runtimeLayerId: input.id,
    sourceId: input.sourceId ?? input.layerDef?.source ?? existing?.sourceId ?? input.id,
    family: input.family ?? familyForLayerType(layerType),
    ownership: 'command',
    persistence: 'session',
    mountMode: 'imperative',
    zGroup: 0,
    seq: existing?.seq ?? ++generation,
    lastTouched: _touch(),
    sourceDef: input.sourceDef ?? existing?.sourceDef,
    layerDef: input.layerDef ?? existing?.layerDef,
    remount: input.remount ?? existing?.remount,
  };
  registry.set(descriptor.runtimeLayerId, descriptor);
  sourceIndex.set(descriptor.sourceId, descriptor.runtimeLayerId);
  evictToBound();
}

/** 挂载缝：记录 source 定义（层未到时建独立账目，键为 source id）。 */
export function recordRuntimeSource(sourceId: string, def: RuntimeLayerSourceDef): void {
  if (!sourceId || !def) return;
  // P5（D-4）：source 定义更新必须传播到**所有**共享该 source 的账目 —
  // 此前只更新 sourceIndex 指向的最后一个注册者，兄弟层（fill+outline
  // 共享 source）持有陈旧 def，style-reload 重放谁先谁赢、数据版本不定。
  let touched = false;
  for (const entry of registry.values()) {
    if (entry.sourceId === sourceId) {
      entry.sourceDef = def;
      entry.lastTouched = _touch();
      touched = true;
    }
  }
  if (touched) return;
  registerRuntimeLayer({ id: sourceId, sourceId, sourceDef: def });
}

/** 挂载缝：记录 layer 定义（吸附同 source 的独立账目）。 */
export function recordRuntimeLayer(def: RuntimeLayerDef): void {
  if (!def?.id) return;
  registerRuntimeLayer({
    id: def.id,
    sourceId: def.source,
    layerDef: def,
  });
}

/** 命令路径：登记重挂闭包（定义重放的兜底）。 */
export function rememberRuntimeRemount(id: string, remount: (map: any) => void): void {
  if (!id || typeof remount !== 'function') return;
  const existing = registry.get(id) ?? findEntryBySource(id);
  if (existing) {
    existing.remount = remount;
    existing.lastTouched = _touch();
    return;
  }
  registerRuntimeLayer({ id, remount });
}

/** 命令删除覆盖层时反注册（层族前缀清扫；source 记在层账目内一并移除）。幂等。 */
export function unregisterRuntimeLayer(layerId: string): void {
  if (!layerId) return;
  // 先快照将被清扫的账目（所有权转移需要 dropEntry 之前的 sourceDef）。
  const dropped: RuntimeLayerDescriptor[] = [];
  for (const id of [...registry.keys()]) {
    if (id === layerId || id.startsWith(`${layerId}-`) || id.startsWith(`${layerId}__`)) {
      const entry = registry.get(id);
      if (entry) dropped.push({ ...entry, sourceDef: entry.sourceDef });
      dropEntry(id);
    }
  }
  if (dropped.length === 0) return;
  // source 所有权转移（ADR-0081 Source Lifecycle follow-up）：被清扫的账目若
  // 持有 sourceDef 且仍有兄弟层引用同一 source，把定义移交存活者 —— 否则
  // 主层反注册后共享层的 style-reload 重放会因 source 缺失而静默失败
  //（review 记录的防御性缺口：当前命令不铸造多层层共享 source，但账本
  // 语义必须自洽）。
  for (const entry of dropped) {
    _transferSourceOwnership(entry);
  }
}

/** 被移除账目的 sourceDef 移交给仍引用该 source 且无定义的存活层。 */
function _transferSourceOwnership(dropped: RuntimeLayerDescriptor): void {
  if (!dropped.sourceDef) return;
  if (registry.get(dropped.runtimeLayerId)) return;
  const survivor = [...registry.values()].find(
    (e) => e.sourceId === dropped.sourceId && !e.sourceDef && e.layerDef,
  );
  if (survivor) {
    survivor.sourceDef = dropped.sourceDef;
    sourceIndex.set(survivor.sourceId, survivor.runtimeLayerId);
  }
}

/**
 * 可见性记账（ADR-0081）：renderer 的 setLayoutProperty('visibility') 之后
 * 同步 layerDef.layout —— style reload 的定义重放不再把隐藏中的命令层
 * 复活为可见（此前 layerDef 记录于挂载时刻，不含后续 layout 变更）。
 */
export function recordRuntimeLayerVisibility(layerId: string, visible: boolean): void {
  const entry = registry.get(layerId);
  if (!entry?.layerDef) return;
  entry.lastTouched = _touch(); // 可见性变更也是活跃信号（LRU）
  entry.layerDef = {
    ...entry.layerDef,
    layout: { ...entry.layerDef.layout, visibility: visible ? 'visible' : 'none' },
  };
}

/** 会话切换：清空全部命令层账目（旧会话命令层不得复活进新会话）。 */
export function clearRuntimeLayerRegistry(): void {
  registry.clear();
  sourceIndex.clear();
  evictedLog.length = 0;
}

/** P5：最近被驱逐的层 id（有界环，最旧在前；诊断/reconcile 观测点）。 */
export function recentlyEvictedLayerIds(): string[] {
  return [...evictedLog];
}

/** P5：该层是否被容量驱逐过（真值只在清空前有效 —— 会话切换即失效）。 */
export function wasRuntimeLayerEvicted(layerId: string): boolean {
  return evictedLog.includes(layerId);
}

/**
 * style reload 后重放（幂等：缺失才补，存在则跳过）。先 sources 后
 * layers（MapLibre 层引用 source，顺序反了会 throw）。定义重放优先；
 * 无 layerDef 但有闭包的条目走闭包兜底。返回重挂层数（观测点）。
 */
export function remountRuntimeLayers(
  map: any,
  hooks?: { onLayerAdded?: (map: any, id: string) => void },
): number {
  if (!map) return 0;
  let remounted = 0;
  for (const entry of registry.values()) {
    const def = entry.sourceDef;
    if (!def || map.getSource?.(entry.sourceId)) continue;
    try {
      if (def.kind === 'geojson') {
        map.addSource(entry.sourceId, { type: 'geojson', data: def.data });
      } else if (def.kind === 'image' && def.url && def.coordinates) {
        map.addSource(entry.sourceId, { type: 'image', url: def.url, coordinates: def.coordinates });
      }
    } catch { /* 竞态下已被补挂/样式未就绪 —— 下次重放再试 */ }
  }
  for (const entry of registry.values()) {
    if (map.getLayer?.(entry.runtimeLayerId)) continue;
    const def = entry.layerDef;
    if (def) {
      try {
        const layer: Record<string, unknown> = {
          id: def.id,
          type: def.type,
          source: def.source,
        };
        if (def.paint) layer.paint = def.paint;
        if (def.layout) layer.layout = def.layout;
        if (def.filter !== undefined) layer.filter = def.filter;
        map.addLayer(layer as any, def.beforeId ?? undefined);
        // 重挂的层必须补记 style-layer-id 账本 —— 否则下一次
        // layer-changing reconcile 的 z 序同步看不到 custom 层（#461）。
        hooks?.onLayerAdded?.(map, def.id);
        remounted += 1;
      } catch { /* 同上 */ }
    } else if (typeof entry.remount === 'function') {
      try {
        entry.remount(map);
        remounted += 1;
      } catch { /* 单项失败不阻断其余项 */ }
    }
  }
  return remounted;
}

/** 观测/测试：当前登记的层描述符快照（插入序）。 */
export function describeRuntimeLayers(): RuntimeLayerDescriptor[] {
  return [...registry.values()].map((entry) => ({ ...entry }));
}

/** 观测/测试：层 id 列表（有 layerDef 的条目，插入序）。 */
export function listRuntimeLayerIds(): string[] {
  return [...registry.values()]
    .filter((entry) => entry.layerDef)
    .map((entry) => entry.runtimeLayerId);
}

/** 测试隔离。 */
export function resetRuntimeLayerRegistry(): void {
  registry.clear();
  sourceIndex.clear();
  evictedLog.length = 0;
}

/** 测试观测：登记总数。 */
export function runtimeLayerCount(): number {
  return registry.size;
}

/** 观测：带兜底闭包的条目数（命令路径 remember 登记）。 */
export function runtimeRemountProviderCount(): number {
  let n = 0;
  for (const entry of registry.values()) {
    if (typeof entry.remount === 'function') n += 1;
  }
  return n;
}
