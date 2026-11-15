/**
 * G40 live 资产注册生命周期（WP06）：
 *
 * - **内容缓存**：进程级 LRU（digest, paramsKey, ratio）→ ImageData（有界
 *   ≤32MB / ≤256 项；同资产多实例去重，S25）。
 * - **每 map 实例注册**：`ensureRegistered(map, doc, params)` 幂等；引用
 *   计数跨实例共享 image id（S16：关其一不清另一）；实例销毁
 *   `releaseMap(map)` 只移除本实例引用，末位引用才 removeImage。
 * - **迟到防护**（S17）：resolve 的内容 digest 与当前注册时 pin 不符 →
 *   丢弃（不注册到新会话、不覆盖新绑定）。
 * - **style reload**（S15）：`reinstallAll(map)` 在 style 重建后重装；
 *   MapSpecRuntime.invalidateStyle 后由接线方调用。
 *
 * 失败面（S23）：解码/注册失败 → typed {code}，绝不默认圆伪装。
 */

import type { Map as MapLibreMap } from 'maplibre-gl';

import {
  AssetKind,
  PointParams,
  SymbolAssetDocument,
  TextureParams,
  normalizePointParams,
  normalizeTextureParams,
  patternImageId,
  pointImageId,
  stableKey,
} from './contracts';
import { rasterizeAreaTexture, rasterizePointSymbol } from './rasterize';

export const PIXEL_RATIO = 2;
const MAX_CACHE_ITEMS = 256;
const MAX_CACHE_BYTES = 32 * 1024 * 1024;

export interface RegistrationFailure {
  code:
    | 'ASSET_DOC_INVALID'
    | 'ASSET_RASTER_FAILED'
    | 'ASSET_REGISTER_FAILED'
    | 'ASSET_MAP_MISSING';
  message: string;
  imageId?: string;
}

interface CacheEntry {
  image: ImageData;
  bytes: number;
}

const contentCache = new Map<string, CacheEntry>();
let cacheBytes = 0;

/** map 实例 → image id 集合（引用计数表；进程内）。 */
const mapRefs = new Map<MapLibreMap, Set<string>>();
/** image id → 持有该 image 的 map 实例数。 */
const idOwners = new Map<string, Set<MapLibreMap>>();

function cachePut(key: string, image: ImageData): void {
  if (contentCache.has(key)) return;
  const bytes = image.data.length;
  while (
    contentCache.size >= MAX_CACHE_ITEMS ||
    cacheBytes + bytes > MAX_CACHE_BYTES
  ) {
    const first = contentCache.keys().next();
    if (first.done) break;
    cacheBytes -= contentCache.get(first.value)?.bytes ?? 0;
    contentCache.delete(first.value);
  }
  contentCache.set(key, { image, bytes });
  cacheBytes += bytes;
}

export interface AssetRegistration {
  imageId: string;
  kind: AssetKind;
}

/**
 * 确保图像已注册到 map（幂等；返回注册名或 typed 失败）。
 * digestPin：调用方（MapSpec 绑定）的 content_digest —— 与 doc 不符即拒
 * （迟到/串内容防护，S17/S23）。
 */
export function ensureRegistered(
  map: MapLibreMap | null | undefined,
  doc: SymbolAssetDocument,
  rawParams: Record<string, unknown>,
  digestPin?: string,
): AssetRegistration | RegistrationFailure {
  if (!map) return { code: 'ASSET_MAP_MISSING', message: 'map 实例不可用' };
  if (digestPin && doc.content_digest !== digestPin) {
    return {
      code: 'ASSET_DOC_INVALID',
      message: `digest pin 不符：期望 ${digestPin.slice(0, 19)} 实得 ${doc.content_digest.slice(0, 19)}`,
    };
  }
  let params: PointParams | TextureParams;
  let imageId: string;
  try {
    if (doc.kind === 'point_symbol') {
      params = normalizePointParams(rawParams, doc);
      imageId = pointImageId(doc.content_digest, params as unknown as Record<string, unknown>);
    } else {
      params = normalizeTextureParams(rawParams, doc);
      imageId = patternImageId(doc.content_digest, params as unknown as Record<string, unknown>);
    }
  } catch (err) {
    return {
      code: 'ASSET_DOC_INVALID',
      message: err instanceof Error ? err.message : String(err),
    };
  }
  const cacheKey = `${doc.content_digest}|${stableKey(params)}|${PIXEL_RATIO}`;
  let entry = contentCache.get(cacheKey);
  if (!entry) {
    try {
      const image = doc.kind === 'point_symbol'
        ? rasterizePointSymbol(doc, params as PointParams, PIXEL_RATIO)
        : rasterizeAreaTexture(doc, params as TextureParams, PIXEL_RATIO);
      entry = { image, bytes: image.data.length };
      cachePut(cacheKey, image);
    } catch (err) {
      return {
        code: 'ASSET_RASTER_FAILED',
        message: err instanceof Error ? err.message : String(err),
        imageId,
      };
    }
  }
  try {
    if (!map.hasImage(imageId)) {
      map.addImage(imageId, entry.image, { pixelRatio: PIXEL_RATIO, sdf: false });
    }
  } catch (err) {
    return {
      code: 'ASSET_REGISTER_FAILED',
      message: err instanceof Error ? err.message : String(err),
      imageId,
    };
  }
  // 引用记账（同 map 重复注册幂等）
  let refs = mapRefs.get(map);
  if (!refs) {
    refs = new Set();
    mapRefs.set(map, refs);
  }
  refs.add(imageId);
  let owners = idOwners.get(imageId);
  if (!owners) {
    owners = new Set();
    idOwners.set(imageId, owners);
  }
  owners.add(map);
  return { imageId, kind: doc.kind };
}

/** map 实例销毁（frame 关闭）：只影响本实例持有的引用。 */
export function releaseMap(map: MapLibreMap): void {
  const refs = mapRefs.get(map);
  if (!refs) return;
  for (const imageId of refs) {
    const owners = idOwners.get(imageId);
    if (!owners) continue;
    owners.delete(map);
    if (owners.size === 0) {
      idOwners.delete(imageId);
      try {
        if (map.hasImage(imageId)) map.removeImage(imageId);
      } catch {
        // map 已销毁 —— 无需清理
      }
    }
  }
  mapRefs.delete(map);
}

/** style 重建后重装本实例全部资产（S15；map 上已注册名被 style 清空）。 */
export function reinstallAll(
  map: MapLibreMap,
  resolveDoc: (imageId: string) => SymbolAssetDocument | null,
  resolveParams: (imageId: string) => Record<string, unknown> | null,
): { reinstalled: number; failures: RegistrationFailure[] } {
  const refs = mapRefs.get(map);
  if (!refs) return { reinstalled: 0, failures: [] };
  let reinstalled = 0;
  const failures: RegistrationFailure[] = [];
  for (const imageId of refs) {
    const doc = resolveDoc(imageId);
    const params = resolveParams(imageId);
    if (!doc || !params) {
      // 重装需要 (digest, params) 反查 —— 由接线方提供注册表快照；
      // 不可得时按缺席披露（不静默丢引用）。
      failures.push({
        code: 'ASSET_DOC_INVALID',
        message: `reinstall 反查缺席: ${imageId}`,
        imageId,
      });
      continue;
    }
    const cacheKey = `${doc.content_digest}|${stableKey(params)}|${PIXEL_RATIO}`;
    const entry = contentCache.get(cacheKey);
    if (!entry) {
      failures.push({
        code: 'ASSET_RASTER_FAILED',
        message: `reinstall 缓存缺席: ${imageId}`,
        imageId,
      });
      continue;
    }
    try {
      if (!map.hasImage(imageId)) {
        map.addImage(imageId, entry.image, {
          pixelRatio: PIXEL_RATIO, sdf: false,
        });
      }
      reinstalled += 1;
    } catch (err) {
      failures.push({
        code: 'ASSET_REGISTER_FAILED',
        message: err instanceof Error ? err.message : String(err),
        imageId,
      });
    }
  }
  return { reinstalled, failures };
}

/** 诊断面：缓存/引用快照（测试与状态披露用）。 */
export function registrationStats(): {
  cacheItems: number;
  cacheBytes: number;
  maps: number;
  images: number;
} {
  return {
    cacheItems: contentCache.size,
    cacheBytes,
    maps: mapRefs.size,
    images: idOwners.size,
  };
}
