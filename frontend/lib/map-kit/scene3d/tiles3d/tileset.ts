/**
 * scene3d/tiles3d/tileset — OGC 3D Tiles 1.1 受控子集解析（G3D02 M3）。
 *
 * 能力边界（typed 拒绝）：
 * - tileset.json 入口；boundingVolume 支持 region / box / sphere；
 *   refine REPLACE 与 ADD；content.uri（1.1 glTF/GLB 内容）。
 * - **不支持**（TilesetParseError.unsupported_feature 显式拒绝，不静默）：
 *   b3dm/i3dm/pnts/cmpt 旧版二进制内容、external tileset（content.uri 以
 *   .json 结尾的子树）、1.0 `content.url`、`3DTILES_` 内容扩展。
 * - transform 链：tileset 根 transform 依规范是 tile 局部（z-up ENU 米制）
 *   → ECEF；锚定渲染把整棵子树经 ECEF→ENU(anchor) 换算进放置矩阵。
 *   无 transform（恒等）= tile 局部即锚点 ENU（常见于本地生成器）。
 */

import type { Mat4, WorldCoord } from '../geo';
import {
  enuFromEcefRotation,
  identityMat4,
  multiplyMat4,
  transformPoint,
} from '../geo';

export type TilesetRejectCode =
  | 'not_tileset'
  | 'version_unsupported'
  | 'unsupported_feature'
  | 'corrupt'
  | 'budget_exceeded';

export class TilesetParseError extends Error {
  readonly code: TilesetRejectCode;
  constructor(code: TilesetRejectCode, detail: string) {
    super(`3D Tiles parse ${code}: ${detail}`);
    this.name = 'TilesetParseError';
    this.code = code;
  }
}

export const MAX_TILES_PER_TILESET = 8192;

export interface WorldSphere {
  center: WorldCoord;
  /** 世界单位半径（米/米单位换算由调用方在构造时完成）。 */
  radiusM: number;
}

export interface TileNode {
  /** 稳定 id（遍历路径，如 "0/1/2"）。 */
  id: string;
  /** tile 内容 URI（绝对化后；叶子无内容 = 纯结构节点）。 */
  contentUri: string | null;
  /** glTF 内容的字节区间提示（GLB）或 JSON 提示（gltf）。 */
  contentFormat: 'glb' | 'gltf' | null;
  geometricError: number;
  refine: 'REPLACE' | 'ADD';
  /** tile 局部（米）→ ENU(anchor)（含根 transform 的 ECEF→ENU 折算）。 */
  localToEnu: Mat4;
  /** 包围球（ENU 米制 —— SSE 与剔除消费）。 */
  sphereEnu: { cx: number; cy: number; cz: number; radiusM: number };
  /** tile 级元数据（extras —— pick 披露面）。 */
  extras: unknown;
  children: TileNode[];
}

export interface ParsedTileset {
  root: TileNode;
  tileCount: number;
  rootGeometricError: number;
  /** tileset 根是否携带非恒等 ECEF transform（锚定换算已折入 localToEnu）。 */
  hasEcefTransform: boolean;
  assetVersion: string;
}

interface RawTile {
  transform?: number[];
  boundingVolume?: {
    region?: number[];
    box?: number[];
    sphere?: number[];
  };
  geometricError?: number;
  refine?: string;
  content?: { uri?: string; url?: string };
  children?: RawTile[];
  extras?: unknown;
}

interface RawTileset {
  asset?: { version?: string };
  geometricError?: number;
  root?: RawTile;
  extensionsRequired?: string[];
}

export async function fetchTilesetJson(
  tilesetUrl: string,
  fetcher: (url: string) => Promise<unknown>,
): Promise<unknown> {
  return fetcher(tilesetUrl);
}

/** 解析 tileset 文档。anchor = 资产放置锚点（region→ENU 换算基准）。 */
export function parseTileset(
  doc: unknown,
  opts: {
    tilesetUrl: string;
    /** 锚点（度）；ECEF transform 折算 + region 包围的参考点。 */
    anchor: { lng: number; lat: number };
    /** 相对 URI 绝对化（resource.resolveUri；测试注入）。 */
    resolveUri: (base: string, uri: string) => string | null;
  },
): ParsedTileset {
  if (typeof doc !== 'object' || doc === null) {
    throw new TilesetParseError('not_tileset', 'tileset root is not an object');
  }
  const ts = doc as RawTileset;
  const version = ts.asset?.version ?? '';
  if (!version.startsWith('1.')) {
    throw new TilesetParseError('version_unsupported', `asset.version '${version}'`);
  }
  if (ts.extensionsRequired?.length) {
    throw new TilesetParseError(
      'unsupported_feature',
      `extensionsRequired: ${ts.extensionsRequired.join(',')}`,
    );
  }
  if (!ts.root) throw new TilesetParseError('corrupt', 'missing root tile');
  const rootGe = ts.geometricError ?? ts.root.geometricError;
  if (typeof rootGe !== 'number' || !(rootGe > 0)) {
    throw new TilesetParseError('corrupt', 'tileset geometricError invalid');
  }

  // 根 transform：tile 局部 → ECEF（或恒等 = 局部即 ENU）。
  let rootTransform: Mat4 | null = null;
  if (Array.isArray(ts.root.transform) && ts.root.transform.length === 16) {
    rootTransform = ts.root.transform.slice() as Mat4;
  }
  const identity = identityMat4();
  const hasEcefTransform = !!rootTransform && !matEquals(rootTransform, identity);
  // ECEF→ENU(anchor) 折算：localToEnu = R_enu_from_ecef · T_root（仅根层；
  // 子 tile transform 相对根，已含在乘链里）。
  const ecefToEnu = hasEcefTransform
    ? enuFromEcefRotation(opts.anchor.lat, opts.anchor.lng)
    : identity;
  const rootLocalToEnu = multiplyMat4(ecefToEnu, rootTransform ?? identity);

  let count = 0;
  const root = parseTile(ts.root, '0', opts, rootLocalToEnu, (t) => {
    count += 1;
    if (count > MAX_TILES_PER_TILESET) {
      throw new TilesetParseError('budget_exceeded', `tiles > ${MAX_TILES_PER_TILESET}`);
    }
    return t;
  });
  return {
    root,
    tileCount: count,
    rootGeometricError: rootGe,
    hasEcefTransform,
    assetVersion: version,
  };
}

function parseTile(
  raw: RawTile,
  id: string,
  opts: { tilesetUrl: string; anchor: { lng: number; lat: number }; resolveUri: (b: string, u: string) => string | null },
  parentToEnu: Mat4,
  tick: (t: TileNode) => TileNode,
): TileNode {
  const tileTransform = Array.isArray(raw.transform) && raw.transform.length === 16
    ? (raw.transform.slice() as Mat4)
    : identityMat4();
  const localToEnu = multiplyMat4(parentToEnu, tileTransform);

  const bv = raw.boundingVolume;
  if (!bv) throw new TilesetParseError('corrupt', `tile ${id} missing boundingVolume`);
  let sphereEnu: { cx: number; cy: number; cz: number; radiusM: number };
  if (Array.isArray(bv.region) && bv.region.length === 6) {
    // region: [w, s, e, n, minH, maxH]（WGS84 度 + 米）→ 相对锚点的 ENU 球。
    sphereEnu = regionToEnuSphere(bv.region, opts.anchor);
  } else if (Array.isArray(bv.box) && bv.box.length === 12) {
    sphereEnu = boxToEnuSphere(bv.box, localToEnu);
  } else if (Array.isArray(bv.sphere) && bv.sphere.length === 4) {
    const [cx, cy, cz, r] = bv.sphere;
    const c = transformPoint(localToEnu, { x: cx, y: cy, z: cz });
    // 旋转不改变半径；均匀缩放会 —— tile transform 的缩放分量按最大轴幅换算
    const scale = maxAxisScale(localToEnu);
    sphereEnu = { cx: c.x, cy: c.y, cz: c.z, radiusM: r * scale };
  } else {
    throw new TilesetParseError('corrupt', `tile ${id} boundingVolume shape invalid`);
  }

  let contentUri: string | null = null;
  let contentFormat: 'glb' | 'gltf' | null = null;
  const content = raw.content;
  if (content && typeof content === 'object') {
    if (typeof content.url === 'string') {
      throw new TilesetParseError('unsupported_feature', '1.0 content.url not supported (use 1.1 content.uri)');
    }
    const uri = content.uri;
    if (typeof uri === 'string' && uri.length > 0) {
      if (/\.json(\?|$)/i.test(uri)) {
        throw new TilesetParseError('unsupported_feature', `external tileset content '${uri}' not supported`);
      }
      if (/\.(b3dm|i3dm|pnts|cmpt)(\?|$)/i.test(uri)) {
        throw new TilesetParseError('unsupported_feature', `legacy binary content '${uri}' not supported (3D Tiles 1.1 glTF content only)`);
      }
      const resolved = opts.resolveUri(opts.tilesetUrl, uri);
      if (!resolved) {
        throw new TilesetParseError('corrupt', `content uri '${uri}' rejected by resource policy`);
      }
      contentUri = resolved;
      contentFormat = /\.gltf(\?|$)/i.test(uri) ? 'gltf' : 'glb';
    }
  }

  const refineRaw = typeof raw.refine === 'string' ? raw.refine.toUpperCase() : null;
  const refine: 'REPLACE' | 'ADD' = refineRaw === 'ADD' ? 'ADD' : 'REPLACE';

  const node: TileNode = {
    id,
    contentUri,
    contentFormat,
    geometricError: typeof raw.geometricError === 'number' && raw.geometricError >= 0 ? raw.geometricError : 0,
    refine,
    localToEnu,
    sphereEnu,
    extras: raw.extras,
    children: [],
  };
  const children = raw.children ?? [];
  node.children = children.map((c, i) => parseTile(c, `${id}/${i}`, opts, localToEnu, tick));
  return tick(node);
}

/** region（WGS84 度 + 米高程）→ 锚点 ENU 包围球（小范围平面近似）。 */
function regionToEnuSphere(
  region: number[],
  anchor: { lng: number; lat: number },
): { cx: number; cy: number; cz: number; radiusM: number } {
  const [w, s, e, n, minH, maxH] = region;
  const meanLat = (s + n) / 2;
  const meanLng = (w + e) / 2;
  const DEG = Math.PI / 180;
  const metersPerDegLat = 111_320.0;
  const metersPerDegLng = 111_320.0 * Math.cos(meanLat * DEG);
  const cx = (meanLng - anchor.lng) * metersPerDegLng;
  const cy = (meanLat - anchor.lat) * metersPerDegLat;
  const cz = (minH + maxH) / 2;
  const halfX = Math.abs(e - w) / 2 * metersPerDegLng;
  const halfY = Math.abs(n - s) / 2 * metersPerDegLat;
  const halfZ = Math.abs(maxH - minH) / 2;
  const radius = Math.sqrt(halfX * halfX + halfY * halfY + halfZ * halfZ);
  return { cx, cy, cz, radiusM: Math.max(radius, 1) };
}

/** box（center + 3 半轴向量，tile 局部）→ ENU 包围球。 */
function boxToEnuSphere(
  box: number[],
  localToEnu: Mat4,
): { cx: number; cy: number; cz: number; radiusM: number } {
  const center = transformPoint(localToEnu, { x: box[0], y: box[1], z: box[2] });
  const axes: WorldCoord[] = [
    { x: box[3], y: box[4], z: box[5] },
    { x: box[6], y: box[7], z: box[8] },
    { x: box[9], y: box[10], z: box[11] },
  ];
  const scale = maxAxisScale(localToEnu);
  let maxR = 0;
  for (const a of axes) maxR = Math.max(maxR, Math.hypot(a.x, a.y, a.z));
  return { cx: center.x, cy: center.y, cz: center.z, radiusM: Math.max(maxR * scale, 1) };
}

function maxAxisScale(m: Mat4): number {
  const lens = [
    Math.hypot(m[0], m[1], m[2]),
    Math.hypot(m[4], m[5], m[6]),
    Math.hypot(m[8], m[9], m[10]),
  ];
  return Math.max(...lens, 1e-9);
}

function matEquals(a: Mat4, b: Mat4): boolean {
  for (let i = 0; i < 16; i++) if (a[i] !== b[i]) return false;
  return true;
}
