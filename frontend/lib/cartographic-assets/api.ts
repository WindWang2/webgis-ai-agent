/**
 * G40 资产 API 客户端 + 规范形 doc 预热缓存。
 *
 * - API 面：/api/v1/cartographic-assets（列表/详情/内容/会话绑定）——与
 *   后端 app/api/routes/cartographic_assets.py 对齐；
 * - doc 缓存：content_digest → SymbolAssetDocument（进程级 LRU，跨 map
 *   实例共享；内容寻址 → 无跨会话泄漏面，绑定侧 digest pin 保证不串内容）。
 */

import { apiFetch } from '@/lib/api/transport';
import { SymbolAssetBinding, SymbolAssetDocument } from './contracts';

export interface AssetSummary {
  asset_id: string;
  kind: string;
  name: string;
  builtin: boolean;
  revision: number;
  current_revision: number;
  latest_revision: number;
  content_digest: string;
  status?: string;
  default_params?: Record<string, unknown>;
}

export interface BindingReceipt {
  success: boolean;
  layer_id: string;
  committed: boolean;
  revision?: number;
  asset?: {
    asset_id: string;
    revision: number;
    kind: string;
    content_digest: string;
    name?: string;
    builtin?: boolean;
  };
  params?: Record<string, unknown>;
  category_binding?: { rules: number; field: string; other_mode: string };
  binding_fingerprint?: string;
  disclosures?: string[];
  preview?: { swatch: Record<string, unknown> };
  can_undo?: boolean;
  unbound?: boolean;
  restored_paint?: boolean;
  code?: string;
  message?: string;
  correction_hint?: string;
}

// ─── doc 预热缓存（digest 寻址） ──────────────────────────────────────────

const docCache = new Map<string, SymbolAssetDocument>();
const MAX_DOCS = 128;

export function cachedDoc(digest: string): SymbolAssetDocument | null {
  const doc = docCache.get(digest);
  if (!doc || doc.content_digest !== digest) return null;
  // LRU touch
  docCache.delete(digest);
  docCache.set(digest, doc);
  return doc;
}

function putDoc(doc: SymbolAssetDocument): void {
  if (docCache.has(doc.content_digest)) return;
  docCache.set(doc.content_digest, doc);
  while (docCache.size > MAX_DOCS) {
    const first = docCache.keys().next();
    if (first.done) break;
    docCache.delete(first.value);
  }
}

function seedBuiltin(list: AssetSummary[]): void {
  // builtin 资产规范形随列表响应内联（服务端 builtin_index 附带）——
  // 无需逐个请求内容端点。
  for (const item of list) {
    if (item.builtin && (item as any).document) {
      try {
        putDoc((item as any).document as SymbolAssetDocument);
      } catch {
        // 形状异常 —— 留给内容端点路径
      }
    }
  }
}

/** 收集 MapSpec 里全部绑定引用的 digest（含类别规则/other）。 */
export function bindingDigests(layers: Array<{ symbol_asset?: unknown }>): string[] {
  const out = new Set<string>();
  for (const layer of layers) {
    const b = layer.symbol_asset as SymbolAssetBinding | undefined;
    if (!b || typeof b !== 'object') continue;
    if (b.content_digest) out.add(b.content_digest);
    const cb = b.category_binding;
    if (cb) {
      for (const r of cb.map ?? []) if (r.content_digest) out.add(r.content_digest);
      if (cb.other_mode === 'fallback_asset' && cb.other_content_digest) {
        out.add(cb.other_content_digest);
      }
    }
  }
  return [...out];
}

export interface WarmResult {
  ready: string[];
  missing: Array<{ digest: string; reason: string }>;
}

/**
 * 预热：builtin 列表 + 缺席 digest 的内容拉取。迟到结果安全：只填充
 * digest 寻址缓存（S17 —— 不绑定到任何特定会话）。
 */
export async function warmDocs(digests: string[], sessionId?: string): Promise<WarmResult> {
  const missing: Array<{ digest: string; reason: string }> = [];
  const need = digests.filter((d) => !cachedDoc(d));
  if (need.length === 0) return { ready: digests, missing };
  // builtin 一次列表（含内联规范形）覆盖大部分；DB 资产走内容端点需要
  // (asset_id, revision) —— 由调用方经 fetchDocByRef 提供。
  try {
    const list = await listAssets();
    seedBuiltin(list);
  } catch {
    // 列表失败不阻断 —— 逐 digest 路径继续
  }
  for (const digest of need) {
    if (cachedDoc(digest)) continue;
    missing.push({ digest, reason: 'not_builtin' });
  }
  return {
    ready: digests.filter((d) => !!cachedDoc(d)),
    missing,
  };
}

/** 按 (asset_id, revision) 拉取规范形（DB 资产路径；scope 服务端检查）。 */
export async function fetchDocByRef(
  assetId: string, revision: number, expectedDigest?: string,
): Promise<SymbolAssetDocument> {
  const doc = await apiFetch<SymbolAssetDocument>(
    `/api/v1/cartographic-assets/${encodeURIComponent(assetId)}` +
    `/versions/${revision}/content`);
  if (!doc || (expectedDigest && doc.content_digest !== expectedDigest)) {
    throw new Error('content_digest mismatch');
  }
  putDoc(doc);
  return doc;
}

/** MapSpec 层绑定 → {digest → doc}（缺席条目经 ref 拉取；失败如实 missing）。 */
export async function resolveLayerDocs(
  layers: Array<{ symbol_asset?: unknown }>,
  sessionId?: string,
): Promise<{ docs: Map<string, SymbolAssetDocument>; missing: WarmResult['missing'] }> {
  const digestByRef = new Map<string, { assetId: string; revision: number }>();
  const wanted = new Set<string>();
  for (const layer of layers) {
    const b = layer.symbol_asset as (SymbolAssetBinding & {
      category_binding?: SymbolAssetBinding['category_binding'];
    }) | undefined;
    if (!b) continue;
    const refs: Array<{ assetId: string; revision: number; digest: string }> = [
      { assetId: b.asset_id, revision: b.revision, digest: b.content_digest },
      ...(b.category_binding?.map ?? []).map((r) => ({
        assetId: r.asset_id, revision: r.revision, digest: r.content_digest,
      })),
    ];
    if (b.category_binding?.other_mode === 'fallback_asset'
        && b.category_binding.other_asset && b.category_binding.other_content_digest) {
      refs.push({
        assetId: b.category_binding.other_asset,
        revision: b.category_binding.other_revision ?? 1,
        digest: b.category_binding.other_content_digest,
      });
    }
    for (const ref of refs) {
      wanted.add(ref.digest);
      digestByRef.set(ref.digest, { assetId: ref.assetId, revision: ref.revision });
    }
  }
  await warmDocs([...wanted], sessionId);
  const docs = new Map<string, SymbolAssetDocument>();
  const missing: WarmResult['missing'] = [];
  for (const digest of wanted) {
    const doc = cachedDoc(digest);
    if (doc) {
      docs.set(digest, doc);
      continue;
    }
    const ref = digestByRef.get(digest);
    if (!ref) {
      missing.push({ digest, reason: 'no_ref' });
      continue;
    }
    try {
      docs.set(digest, await fetchDocByRef(ref.assetId, ref.revision, digest));
    } catch (err) {
      missing.push({
        digest, reason: err instanceof Error ? err.message : String(err),
      });
    }
  }
  return { docs, missing };
}

// ─── 目录 / 绑定 API ──────────────────────────────────────────────────────

export async function listAssets(params?: {
  kind?: string; limit?: number; offset?: number;
}): Promise<AssetSummary[]> {
  const q = new URLSearchParams();
  if (params?.kind) q.set('kind', params.kind);
  if (params?.limit) q.set('limit', String(params.limit));
  if (params?.offset) q.set('offset', String(params.offset));
  const body = await apiFetch<{ assets?: AssetSummary[] }>(
    `/api/v1/cartographic-assets?${q.toString()}`);
  return body.assets ?? [];
}

function sessionHeaders(ownerToken?: string | null):
    Record<string, string> {
  return ownerToken ? { 'X-Session-Token': ownerToken } : {};
}

export async function inspectLayerBinding(
  sessionId: string, layerId: string, ownerToken?: string | null,
): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(
    `/api/v1/cartographic-assets/sessions/${encodeURIComponent(sessionId)}` +
    `/layers/${encodeURIComponent(layerId)}/inspect`,
    { headers: sessionHeaders(ownerToken) });
}

export async function previewBinding(
  sessionId: string, layerId: string, plan: Record<string, unknown>,
  ownerToken?: string | null,
): Promise<BindingReceipt> {
  return apiFetch<BindingReceipt>(
    `/api/v1/cartographic-assets/sessions/${encodeURIComponent(sessionId)}` +
    `/layers/${encodeURIComponent(layerId)}/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 ...sessionHeaders(ownerToken) },
      body: JSON.stringify({ plan }),
    });
}

export async function applyBinding(
  sessionId: string, layerId: string, plan: Record<string, unknown>,
  expectedRevision: number, clientMutationId?: string,
  ownerToken?: string | null,
): Promise<BindingReceipt> {
  return apiFetch<BindingReceipt>(
    `/api/v1/cartographic-assets/sessions/${encodeURIComponent(sessionId)}` +
    `/layers/${encodeURIComponent(layerId)}/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 ...sessionHeaders(ownerToken) },
      body: JSON.stringify({
        plan, expected_revision: expectedRevision,
        ...(clientMutationId ? { client_mutation_id: clientMutationId } : {}),
      }),
    });
}

export async function unbindLayer(
  sessionId: string, layerId: string, expectedRevision: number,
  ownerToken?: string | null,
): Promise<BindingReceipt> {
  return apiFetch<BindingReceipt>(
    `/api/v1/cartographic-assets/sessions/${encodeURIComponent(sessionId)}` +
    `/layers/${encodeURIComponent(layerId)}/unbind`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 ...sessionHeaders(ownerToken) },
      body: JSON.stringify({ expected_revision: expectedRevision }),
    });
}
