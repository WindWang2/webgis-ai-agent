/**
 * RenderWorkEnvelope — MapSpec 执行信封的前端镜像 + 有界 store
 * （Goal 03 RenderWork Protocol V1，ADR-0225）。
 *
 * 纪律（与 render-apply-ack.ts 同款）：
 *
 * - **纯投影，零状态反写**：信封是后端 canonical projector 的派生只读
 *   投影（MapSpec 仍是唯一 desired truth）；本模块只存「最新一份信封」
 *   供 ACK 对账 echo —— 绝不从前端重建信封，绝不反写 MapSpec；
 * - **防御式解析**：SSE/restore 载荷是不可信输入 —— 逐字段收窄、数组
 *   有界（≤ manifest 预算）、字符串截断；形状非法 → 拒收（诚实缺席，
 *   绝不半收编）；
 * - **代次门**：只接受 mapspec_revision ≥ 已存 revision 的信封（同代
 *   幂等重放 = 同 work_id，不抖动）；迟到旧代信封绝不覆盖新代；
 * - **会话隔离**：sessionId 变化即重置（旧会话信封不得进入新会话的
 *   ACK 对账 —— INV-2）。
 */

export const RENDER_WORK_SCHEMA_VERSION = 'render_work.v1' as const;

/** 有界预算（镜像后端 render_work_envelope.py 同值契约）。 */
export const MAX_DATA_DEPENDENCIES = 64;
export const MAX_LAYER_WORK = 128;
export const MAX_COMPONENT_WORK = 32;
/** G3DV2-B M1：scene.assets 执行工作项预算（镜像 MAX_SCENE_WORK）。 */
export const MAX_SCENE_WORK = 32;
export const MAX_DIAGNOSTICS = 8;

const MAX_ID = 64;
/** 指纹全长保留（carto-sha256:<64hex> = 77 字符；与后端 _MAX_FINGERPRINT=96 同值）。 */
const MAX_FINGERPRINT = 96;

/** 源类型词表（镜像后端 _SOURCE_KINDS；词表外 → 'unknown'）。 */
const SOURCE_KINDS: ReadonlySet<string> = new Set([
  'geojson', 'vector', 'raster', 'raster-dem',
  'data_fabric', 'wms', 'wmts', 'pmtiles',
  // v1.7（G3D02，G3DV2-B 补齐镜像遗漏）：3D 资产数据依赖。
  'model3d',
]);

export interface RenderWorkDataDependency {
  source_id: string;
  kind: string;
  revision?: string;
  fingerprint?: string;
  ref?: string;
}

export interface RenderWorkLayerItem {
  layer_id: string;
  layer_type: string;
  source_id: string;
  visible: boolean;
}

export interface RenderWorkComponentItem {
  component_id: string;
  component_type: string;
  enabled: boolean;
}

/** G3DV2-B M1：scene3d 资产执行工作项（纯 3D 会话的 ACK 对账面）。 */
export interface RenderWorkSceneAssetItem {
  asset_id: string;
  source_id: string;
  format: string;
  visible: boolean;
  revision?: string;
}

export interface RenderWorkEnvelope {
  schema_version: typeof RENDER_WORK_SCHEMA_VERSION;
  work_id: string;
  session_id: string;
  mapspec_revision: number;
  mapspec_fingerprint: string;
  observation_target_seq: number;
  data_dependencies: RenderWorkDataDependency[];
  layer_work: RenderWorkLayerItem[];
  component_work: RenderWorkComponentItem[];
  scene_work: RenderWorkSceneAssetItem[];
  invalidation: {
    style_only?: boolean;
    data_changed: string[];
    layers_added: string[];
    layers_removed: string[];
    components_changed: string[];
  };
  required_capabilities: string[];
  diagnostics: string[];
  work_fingerprint: string;
}

function boundedId(v: unknown): string {
  return typeof v === 'string' ? v.slice(0, MAX_ID) : '';
}

function boundedFp(v: unknown): string | undefined {
  if (typeof v !== 'string' || !v) return undefined;
  return v.slice(0, MAX_FINGERPRINT);
}

function boundedRev(v: unknown): string | undefined {
  if (typeof v === 'number' && Number.isFinite(v)) return String(Math.max(0, v));
  if (typeof v === 'string' && v) return v.slice(0, MAX_ID);
  return undefined;
}

/** 防御式解析：形状非法 → null（拒收整份，不半收编）。 */
export function parseRenderWorkEnvelope(raw: unknown): RenderWorkEnvelope | null {
  if (typeof raw !== 'object' || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (r.schema_version !== RENDER_WORK_SCHEMA_VERSION) return null;
  const workId = boundedId(r.work_id);
  const workFp = boundedId(r.work_fingerprint);
  const sessionId = boundedId(r.session_id);
  if (!workId || !workFp || !sessionId) return null;
  const revision = typeof r.mapspec_revision === 'number'
    && Number.isFinite(r.mapspec_revision)
    ? Math.max(0, Math.floor(r.mapspec_revision))
    : null;
  if (revision === null) return null;

  const deps: RenderWorkDataDependency[] = [];
  if (Array.isArray(r.data_dependencies)) {
    for (const d of r.data_dependencies) {
      if (deps.length >= MAX_DATA_DEPENDENCIES) break;
      if (typeof d !== 'object' || d === null) continue;
      const e = d as Record<string, unknown>;
      const sid = boundedId(e.source_id);
      if (!sid) continue;
      const kind = typeof e.kind === 'string' && SOURCE_KINDS.has(e.kind)
        ? e.kind
        : 'unknown';
      deps.push({
        source_id: sid,
        kind,
        ...(boundedRev(e.revision) !== undefined
          ? { revision: boundedRev(e.revision) } : {}),
        ...(boundedFp(e.fingerprint) !== undefined
          ? { fingerprint: boundedFp(e.fingerprint) } : {}),
        ...(boundedId(e.ref) ? { ref: boundedId(e.ref) } : {}),
      });
    }
  }

  const layerWork: RenderWorkLayerItem[] = [];
  if (Array.isArray(r.layer_work)) {
    for (const w of r.layer_work) {
      if (layerWork.length >= MAX_LAYER_WORK) break;
      if (typeof w !== 'object' || w === null) continue;
      const e = w as Record<string, unknown>;
      const lid = boundedId(e.layer_id);
      if (!lid) continue;
      layerWork.push({
        layer_id: lid,
        layer_type: boundedId(e.layer_type),
        source_id: boundedId(e.source_id),
        visible: e.visible !== false,
      });
    }
  }

  const componentWork: RenderWorkComponentItem[] = [];
  if (Array.isArray(r.component_work)) {
    for (const w of r.component_work) {
      if (componentWork.length >= MAX_COMPONENT_WORK) break;
      if (typeof w !== 'object' || w === null) continue;
      const e = w as Record<string, unknown>;
      const cid = boundedId(e.component_id);
      if (!cid) continue;
      componentWork.push({
        component_id: cid,
        component_type: boundedId(e.component_type),
        enabled: e.enabled !== false,
      });
    }
  }

  // G3DV2-B M1：scene_work（旧信封缺席 = 空数组 —— additive 解析不拒收）。
  const sceneWork: RenderWorkSceneAssetItem[] = [];
  if (Array.isArray(r.scene_work)) {
    for (const s of r.scene_work) {
      if (sceneWork.length >= MAX_SCENE_WORK) break;
      if (typeof s !== 'object' || s === null) continue;
      const e = s as Record<string, unknown>;
      const aid = boundedId(e.asset_id);
      if (!aid) continue;
      sceneWork.push({
        asset_id: aid,
        source_id: boundedId(e.source_id),
        format: boundedId(e.format),
        visible: e.visible !== false,
        ...(boundedRev(e.revision) !== undefined
          ? { revision: boundedRev(e.revision) } : {}),
      });
    }
  }

  const invRaw = typeof r.invalidation === 'object' && r.invalidation !== null
    ? (r.invalidation as Record<string, unknown>)
    : {};
  const boundedIds = (v: unknown, cap: number): string[] =>
    Array.isArray(v)
      ? v.filter((x): x is string => typeof x === 'string')
          .map((x) => x.slice(0, MAX_ID))
          .slice(0, cap)
      : [];

  return {
    schema_version: RENDER_WORK_SCHEMA_VERSION,
    work_id: workId,
    session_id: sessionId,
    mapspec_revision: revision,
    mapspec_fingerprint: boundedFp(r.mapspec_fingerprint) ?? '',
    observation_target_seq:
      typeof r.observation_target_seq === 'number'
      && Number.isFinite(r.observation_target_seq)
        ? Math.max(0, Math.floor(r.observation_target_seq))
        : 0,
    data_dependencies: deps,
    layer_work: layerWork,
    component_work: componentWork,
    scene_work: sceneWork,
    invalidation: {
      ...(invRaw.style_only === undefined || typeof invRaw.style_only !== 'boolean'
        ? {} : { style_only: invRaw.style_only }),
      data_changed: boundedIds(invRaw.data_changed, MAX_DATA_DEPENDENCIES),
      layers_added: boundedIds(invRaw.layers_added, MAX_LAYER_WORK),
      layers_removed: boundedIds(invRaw.layers_removed, MAX_LAYER_WORK),
      components_changed: boundedIds(invRaw.components_changed, MAX_COMPONENT_WORK),
    },
    required_capabilities: boundedIds(r.required_capabilities, 256),
    diagnostics: boundedIds(r.diagnostics, MAX_DIAGNOSTICS),
    work_fingerprint: workFp,
  };
}

// ── 最新信封 store（模块级单例；代次门 + 会话隔离）────────────────────

interface StoredWork {
  sessionId: string;
  revision: number;
  envelope: RenderWorkEnvelope;
}

let stored: StoredWork | null = null;

/**
 * 收纳一份信封（SSE step_result / map-state restore 两信道调用）。
 *
 * 返回是否收纳：拒收（形状非法 / 会话不匹配 / 迟到旧代）→ false ——
 * 调用方可据此披露（不中断主链路）。
 */
export function ingestRenderWork(
  raw: unknown,
  sessionId: string | undefined | null,
): boolean {
  const envelope = parseRenderWorkEnvelope(raw);
  if (!envelope) return false;
  if (!sessionId || envelope.session_id !== sessionId) return false;
  if (stored && stored.sessionId === sessionId
    && envelope.mapspec_revision < stored.revision) {
    // 迟到旧代：绝不覆盖新代（同代重放幂等 —— 允许同 revision 重收）。
    return false;
  }
  stored = {
    sessionId,
    revision: envelope.mapspec_revision,
    envelope,
  };
  return true;
}

/** 当前会话最新信封（无 / 会话不匹配 → null）。 */
export function getLatestRenderWork(
  sessionId?: string | null,
): RenderWorkEnvelope | null {
  if (!stored) return null;
  if (sessionId && stored.sessionId !== sessionId) return null;
  return stored.envelope;
}

/** 会话切换/测试清空。 */
export function resetRenderWorkStore(): void {
  stored = null;
}

/**
 * ACK work echo 的对账状态：
 * - absent  无信封（旧后端 / 未收纳）—— ACK 不带 work 块；
 * - matched 信封 revision == ACK revision（同代证据）；
 * - stale   信封落后于 ACK revision（ACK 描述的是更新代 —— 信封只进诊断）。
 */
export type WorkEchoStatus = 'absent' | 'matched' | 'stale';

export function workEchoStatus(
  envelope: RenderWorkEnvelope | null,
  ackRevision: number,
): WorkEchoStatus {
  if (!envelope) return 'absent';
  return envelope.mapspec_revision === ackRevision ? 'matched' : 'stale';
}
