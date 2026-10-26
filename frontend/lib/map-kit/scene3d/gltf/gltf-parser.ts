/**
 * scene3d/gltf/gltf-parser — glTF 2.0 / GLB 受控子集解析器（G3D02 M3）。
 *
 * 纯函数、零 THREE/WebGL 依赖（jsdom 可测）。产出普通类型化数组层级
 * （ParsedGltf），由 build.ts 投影为 THREE 对象。
 *
 * 能力边界（typed 拒绝/降级，绝不静默）：
 * - 拒绝（GltfParseError.unsupported_feature）：extensionsRequired 非空
 *   （Draco/量化/KHR_materials_* 等）、外部 image URI、多 primitive 之外的
 *   扩展拓扑。
 * - 降级（warnings，渲染继续）：animations/skins/morph targets 忽略
 *   （静态渲染是合法表达）；缺 NORMAL 用 flat shading；可选扩展忽略。
 * - 恶意文件防护：accessor 字节区间逐个对 bufferView/buffer 越界校验；
 *   三角/顶点/字节数预算（超限 typed 拒绝，防 OOM/显存炸弹）。
 */

import type { Mat4 } from '../geo';
import { identityMat4, multiplyMat4, translationMat4 } from '../geo';

export const MAX_TRIANGLES_PER_ASSET = 5_000_000;
export const MAX_VERTICES_PER_ASSET = 5_000_000;
export const MAX_JSON_BYTES = 8 * 1024 * 1024;
/**
 * 解码预算（review P0）：按**展开后字节数**计（Float32 化的 accessor
 * 载荷），在分配前检查 —— 防御「多 primitive 共享同一巨型 accessor」/
 * 「无 bufferView 谎报 count」两类放大炸弹（分配后才检查救不了 OOM）。
 */
export const MAX_DECODED_BYTES_PER_ASSET = 256 * 1024 * 1024;
/** 单 accessor 展开后上限（无 bufferView accessor 的 count 谎报防线）。 */
export const MAX_DECODED_BYTES_PER_ACCESSOR = 64 * 1024 * 1024;

export type GltfRejectCode =
  | 'not_gltf'
  | 'version_unsupported'
  | 'unsupported_feature'
  | 'corrupt'
  | 'budget_exceeded';

export class GltfParseError extends Error {
  readonly code: GltfRejectCode;
  constructor(code: GltfRejectCode, detail: string) {
    super(`glTF parse ${code}: ${detail}`);
    this.name = 'GltfParseError';
    this.code = code;
  }
}

export interface ParsedPrimitive {
  positions: Float32Array;
  normals: Float32Array | null;
  uvs: Float32Array | null;
  indices: Uint32Array | null;
  materialIndex: number | null;
}

export interface ParsedNode {
  name: string | null;
  extras: unknown;
  matrix: Mat4;
  children: ParsedNode[];
  primitives: ParsedPrimitive[];
}

export interface ParsedMaterial {
  baseColorFactor: [number, number, number, number];
  metallicFactor: number;
  roughnessFactor: number;
  doubleSided: boolean;
  baseColorTextureIndex: number | null;
}

export interface ParsedTexture {
  mimeType: string;
  bytes: Uint8Array;
}

export interface ParsedGltf {
  roots: ParsedNode[];
  materials: ParsedMaterial[];
  textures: ParsedTexture[];
  stats: { triangles: number; vertices: number; meshes: number };
  warnings: string[];
}

/** 外部 .bin buffer 装载器（glTF JSON 形态的 buffer0；GLB 自带 bin 不需要）。 */
export type ExternalBufferLoader = (uri: string) => Promise<ArrayBuffer>;

interface GltfJson {
  asset?: { version?: string };
  scene?: number;
  scenes?: { nodes?: number[] }[];
  nodes?: JsonNode[];
  meshes?: {
    primitives?: {
      attributes?: Record<string, number>;
      indices?: number;
      material?: number;
      targets?: unknown[];
    }[];
  }[];
  materials?: JsonMaterial[];
  accessors?: JsonAccessor[];
  bufferViews?: {
    buffer?: number;
    byteOffset?: number;
    byteLength?: number;
    byteStride?: number;
  }[];
  buffers?: { byteLength?: number; uri?: string }[];
  images?: { mimeType?: string; bufferView?: number; uri?: string }[];
  textures?: { source?: number; sampler?: number }[];
  animations?: unknown[];
  skins?: unknown[];
  extensionsRequired?: string[];
  extensionsUsed?: string[];
}

interface JsonNode {
  name?: string;
  extras?: unknown;
  children?: number[];
  mesh?: number;
  matrix?: number[];
  translation?: number[];
  rotation?: number[];
  scale?: number[];
}

interface JsonMaterial {
  pbrMetallicRoughness?: {
    baseColorFactor?: number[];
    metallicFactor?: number;
    roughnessFactor?: number;
    baseColorTexture?: { index?: number };
  };
  doubleSided?: boolean;
}

interface JsonAccessor {
  bufferView?: number;
  byteOffset?: number;
  componentType: number;
  count: number;
  type: string;
}

const COMPONENT_BYTES: Record<number, number> = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 };
const TYPE_ARITY: Record<string, number> = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 };

// ── GLB 容器 ─────────────────────────────────────────────────────────────

const GLB_MAGIC = 0x46546c67; // 'glTF'
const CHUNK_JSON = 0x4e4f534a; // 'JSON'
const CHUNK_BIN = 0x004e4942; // 'BIN'

interface GlbContainer {
  json: Uint8Array;
  bin: Uint8Array | null;
}

export function parseGlbContainer(data: ArrayBuffer): GlbContainer {
  const view = new DataView(data);
  if (data.byteLength < 20) throw new GltfParseError('not_gltf', 'GLB too short');
  if (view.getUint32(0, true) !== GLB_MAGIC) throw new GltfParseError('not_gltf', 'bad GLB magic');
  const version = view.getUint32(4, true);
  if (version !== 2) throw new GltfParseError('version_unsupported', `GLB container version ${version}`);
  let offset = 12;
  let json: Uint8Array | null = null;
  let bin: Uint8Array | null = null;
  while (offset + 8 <= data.byteLength) {
    const chunkLen = view.getUint32(offset, true);
    const chunkType = view.getUint32(offset + 4, true);
    offset += 8;
    if (offset + chunkLen > data.byteLength) throw new GltfParseError('corrupt', 'GLB chunk overruns file');
    if (chunkType === CHUNK_JSON) {
      json = new Uint8Array(data, offset, chunkLen);
    } else if (chunkType === CHUNK_BIN) {
      bin = new Uint8Array(data, offset, chunkLen);
    }
    offset += chunkLen;
  }
  if (!json) throw new GltfParseError('corrupt', 'GLB missing JSON chunk');
  return { json, bin };
}

// ── 主入口 ───────────────────────────────────────────────────────────────

export async function parseGltf(
  input: ArrayBuffer,
  opts: { externalBufferLoader?: ExternalBufferLoader } = {},
): Promise<ParsedGltf> {
  const head = new Uint8Array(input, 0, Math.min(4, input.byteLength));
  const isGlb = head[0] === 0x67 && head[1] === 0x6c && head[2] === 0x54 && head[3] === 0x46; // 'glTF'
  let jsonText: string;
  let binChunk: Uint8Array | null = null;
  if (isGlb) {
    const container = parseGlbContainer(input);
    jsonText = new TextDecoder().decode(container.json);
    binChunk = container.bin;
  } else {
    if (input.byteLength > MAX_JSON_BYTES) {
      throw new GltfParseError('budget_exceeded', `glTF JSON ${input.byteLength} > ${MAX_JSON_BYTES}`);
    }
    jsonText = new TextDecoder().decode(input);
  }
  let doc: GltfJson;
  try {
    doc = JSON.parse(jsonText) as GltfJson;
  } catch {
    throw new GltfParseError('corrupt', 'glTF JSON parse failed');
  }
  const major = Number((doc.asset?.version ?? '0').split('.')[0]);
  if (!Number.isFinite(major) || major !== 2) {
    throw new GltfParseError('version_unsupported', `glTF version ${doc.asset?.version ?? 'missing'}`);
  }
  const warnings: string[] = [];
  if (doc.animations?.length) warnings.push('animations_ignored');
  if (doc.skins?.length) warnings.push('skins_ignored');
  for (const ext of doc.extensionsRequired ?? []) {
    throw new GltfParseError('unsupported_feature', `extensionsRequired includes '${ext}'`);
  }

  // ── buffers（仅支持 buffer 0：GLB bin / data URI / 外部装载器）────────
  const buffers: Uint8Array[] = [];
  const declared = doc.buffers ?? [];
  if (declared.length > 1) {
    throw new GltfParseError('unsupported_feature', `multiple buffers (${declared.length})`);
  }
  if (declared.length === 1) {
    const b = declared[0];
    if (binChunk) {
      if (b.byteLength !== undefined && b.byteLength > binChunk.byteLength) {
        throw new GltfParseError('corrupt', 'buffer.byteLength exceeds GLB bin chunk');
      }
      buffers.push(binChunk.subarray(0, b.byteLength ?? binChunk.byteLength));
    } else if (typeof b.uri === 'string' && b.uri.startsWith('data:')) {
      buffers.push(decodeDataUri(b.uri));
    } else if (typeof b.uri === 'string' && opts.externalBufferLoader) {
      const ab = await opts.externalBufferLoader(b.uri);
      buffers.push(new Uint8Array(ab));
    } else {
      throw new GltfParseError('unsupported_feature', 'external buffer without loader');
    }
  }

  // ── images（仅 bufferView 内嵌）────────────────────────────────────────
  const textures: ParsedTexture[] = [];
  for (const img of doc.images ?? []) {
    if (typeof img.bufferView !== 'number') {
      throw new GltfParseError('unsupported_feature', 'external image URI not supported');
    }
    const bytes = sliceBufferView(buffers, doc.bufferViews, img.bufferView);
    textures.push({ mimeType: img.mimeType ?? 'image/png', bytes: new Uint8Array(bytes) });
  }

  // ── materials ──────────────────────────────────────────────────────────
  const materials: ParsedMaterial[] = (doc.materials ?? []).map((m) => {
    const pbr = m.pbrMetallicRoughness ?? {};
    const factor = pbr.baseColorFactor ?? [1, 1, 1, 1];
    return {
      baseColorFactor: [factor[0], factor[1], factor[2], factor[3] ?? 1],
      metallicFactor: pbr.metallicFactor ?? 1,
      roughnessFactor: pbr.roughnessFactor ?? 1,
      doubleSided: m.doubleSided ?? false,
      baseColorTextureIndex: pbr.baseColorTexture?.index ?? null,
    };
  });

  // ── accessors / primitives（越界防护逐个校验）──────────────────────────
  let totalTriangles = 0;
  let totalVertices = 0;
  let meshCount = 0;
  /** 累计解码字节（review P0：分配前预算 —— 多 primitive 共享巨型 accessor
   *  与谎报 count 的两类放大炸弹在此拦截）。 */
  let decodedBytes = 0;

  const decodeAccessor = (index: number, expectNormalizedOk = false): { typed: Float32Array; normalizedUByte: boolean } => {
    const acc = doc.accessors?.[index];
    if (!acc) throw new GltfParseError('corrupt', `accessor ${index} missing`);
    const arity = TYPE_ARITY[acc.type];
    if (!arity) throw new GltfParseError('unsupported_feature', `accessor type ${acc.type}`);
    const compBytes = COMPONENT_BYTES[acc.componentType];
    if (!compBytes) throw new GltfParseError('unsupported_feature', `componentType ${acc.componentType}`);
    const count = acc.count;
    if (!Number.isInteger(count) || count < 0 || count > MAX_VERTICES_PER_ASSET * 4) {
      throw new GltfParseError('corrupt', 'accessor.count invalid');
    }
    const byteLength = compBytes * arity * count;
    // 展开后（Float32 化）大小 —— 分配前检查（review P0-1）。
    const expandedBytes = count * arity * 4;
    if (expandedBytes > MAX_DECODED_BYTES_PER_ACCESSOR
        || decodedBytes + expandedBytes > MAX_DECODED_BYTES_PER_ASSET) {
      throw new GltfParseError(
        'budget_exceeded',
        `accessor ${index} decoded size ${expandedBytes}B exceeds budget (asset total ${decodedBytes}B + ${expandedBytes}B > ${MAX_DECODED_BYTES_PER_ASSET}B)`,
      );
    }
    decodedBytes += expandedBytes;
    if (acc.bufferView === undefined) {
      // sparse/无 bufferView accessor（如 min/max-only）：返回零值（消费面按缺失处理）
      return { typed: new Float32Array(count * arity), normalizedUByte: false };
    }
    const view = sliceBufferView(buffers, doc.bufferViews, acc.bufferView);
    const base = acc.byteOffset ?? 0;
    if (base + byteLength > view.byteLength) {
      throw new GltfParseError('corrupt', `accessor ${index} range [${base}, ${base + byteLength}) overruns bufferView (${view.byteLength})`);
    }
    const out = new Float32Array(count * arity);
    const dv = new DataView(view.buffer, view.byteOffset + base, byteLength);
    if (acc.componentType === 5126) {
      for (let i = 0; i < out.length; i++) out[i] = dv.getFloat32(i * 4, true);
      return { typed: out, normalizedUByte: false };
    }
    if (!expectNormalizedOk) {
      throw new GltfParseError('unsupported_feature', 'normalized accessor outside TEXCOORD subset');
    }
    // 归一化整型（仅 UV 子集：ubyte/ushort → [0,1]）
    if (acc.componentType === 5121) {
      for (let i = 0; i < out.length; i++) out[i] = dv.getUint8(i) / 255;
      return { typed: out, normalizedUByte: true };
    }
    if (acc.componentType === 5123) {
      for (let i = 0; i < out.length; i++) out[i] = dv.getUint16(i * 2, true) / 65535;
      return { typed: out, normalizedUByte: true };
    }
    throw new GltfParseError('unsupported_feature', `normalized componentType ${acc.componentType}`);
  };

  const decodeIndices = (index: number, vertexCount: number): Uint32Array => {
    const acc = doc.accessors?.[index];
    if (!acc) throw new GltfParseError('corrupt', `indices accessor ${index} missing`);
    if (acc.type !== 'SCALAR') throw new GltfParseError('corrupt', 'indices must be SCALAR');
    const count = acc.count;
    // 索引展开 Uint32 —— 分配前预算（同 P0-1）。
    const expandedBytes = count * 4;
    if (expandedBytes > MAX_DECODED_BYTES_PER_ACCESSOR
        || decodedBytes + expandedBytes > MAX_DECODED_BYTES_PER_ASSET) {
      throw new GltfParseError('budget_exceeded', `indices accessor ${index} exceeds decoded budget`);
    }
    decodedBytes += expandedBytes;
    const view = sliceBufferView(buffers, doc.bufferViews, acc.bufferView ?? -1);
    const base = acc.byteOffset ?? 0;
    const out = new Uint32Array(count);
    const dv = new DataView(view.buffer, view.byteOffset + base, view.byteLength - base);
    if (acc.componentType === 5125) {
      if (base + count * 4 > view.byteLength) throw new GltfParseError('corrupt', 'indices overrun');
      for (let i = 0; i < count; i++) out[i] = dv.getUint32(i * 4, true);
    } else if (acc.componentType === 5123) {
      if (base + count * 2 > view.byteLength) throw new GltfParseError('corrupt', 'indices overrun');
      for (let i = 0; i < count; i++) out[i] = dv.getUint16(i * 2, true);
    } else if (acc.componentType === 5121) {
      if (base + count > view.byteLength) throw new GltfParseError('corrupt', 'indices overrun');
      for (let i = 0; i < count; i++) out[i] = dv.getUint8(i);
    } else {
      throw new GltfParseError('unsupported_feature', `indices componentType ${acc.componentType}`);
    }
    // 索引值域防护（越界索引在构建期是 OOB 写路径）
    for (let i = 0; i < out.length; i++) {
      if (out[i] >= vertexCount) {
        throw new GltfParseError('corrupt', `index ${out[i]} >= vertex count ${vertexCount}`);
      }
    }
    return out;
  };

  const parseNode = (index: number, visiting: Set<number>): ParsedNode => {
    if (visiting.has(index)) throw new GltfParseError('corrupt', `node cycle at ${index}`);
    visiting.add(index);
    const n = doc.nodes?.[index];
    if (!n) throw new GltfParseError('corrupt', `node ${index} missing`);
    let matrix: Mat4;
    if (Array.isArray(n.matrix) && n.matrix.length === 16) {
      matrix = n.matrix.slice() as Mat4;
    } else {
      // glTF TRS 语义：M = T · R · S
      const t = n.translation ?? [0, 0, 0];
      const q = n.rotation ?? [0, 0, 0, 1];
      const s = n.scale ?? [1, 1, 1];
      matrix = multiplyMat4(
        translationMat4(t[0], t[1], t[2]),
        multiplyMat4(quatToMat4(q), scaleMat4xyz(s[0], s[1], s[2])),
      );
    }
    const primitives: ParsedPrimitive[] = [];
    if (typeof n.mesh === 'number') {
      meshCount += 1;
      const mesh = doc.meshes?.[n.mesh];
      for (const prim of mesh?.primitives ?? []) {
        if (prim.targets?.length) warnings.push('morph_targets_ignored');
        const posIdx = prim.attributes?.POSITION;
        if (typeof posIdx !== 'number') {
          throw new GltfParseError('corrupt', 'primitive without POSITION');
        }
        const positions = decodeAccessor(posIdx).typed;
        const vertices = positions.length / 3;
        totalVertices += vertices;
        if (totalVertices > MAX_VERTICES_PER_ASSET) {
          throw new GltfParseError('budget_exceeded', `vertices > ${MAX_VERTICES_PER_ASSET}`);
        }
        const normIdx = prim.attributes?.NORMAL;
        const normals = typeof normIdx === 'number' ? decodeAccessor(normIdx).typed : null;
        const uvIdx = prim.attributes?.TEXCOORD_0;
        const uvs = typeof uvIdx === 'number' ? decodeAccessor(uvIdx, true).typed : null;
        let indices: Uint32Array | null = null;
        if (typeof prim.indices === 'number') {
          indices = decodeIndices(prim.indices, vertices);
          totalTriangles += indices.length / 3;
        } else {
          totalTriangles += vertices / 3;
        }
        if (totalTriangles > MAX_TRIANGLES_PER_ASSET) {
          throw new GltfParseError('budget_exceeded', `triangles > ${MAX_TRIANGLES_PER_ASSET}`);
        }
        primitives.push({
          positions,
          normals,
          uvs,
          indices,
          materialIndex: typeof prim.material === 'number' ? prim.material : null,
        });
      }
    }
    const children = (n.children ?? []).map((c) => parseNode(c, visiting));
    visiting.delete(index);
    return {
      name: typeof n.name === 'string' ? n.name : null,
      extras: n.extras,
      matrix,
      children,
      primitives,
    };
  };

  const sceneIndex = doc.scene ?? 0;
  const rootIndices = doc.scenes?.[sceneIndex]?.nodes ?? [];
  if (rootIndices.length === 0) {
    throw new GltfParseError('corrupt', 'scene has no root nodes');
  }
  const roots = rootIndices.map((i) => parseNode(i, new Set()));
  return {
    roots,
    materials,
    textures,
    stats: { triangles: Math.round(totalTriangles), vertices: totalVertices, meshes: meshCount },
    warnings,
  };
}

function scaleMat4xyz(sx: number, sy: number, sz: number): Mat4 {
  const m = identityMat4();
  m[0] = sx; m[5] = sy; m[10] = sz;
  return m;
}

function quatToMat4(q: number[]): Mat4 {
  const [x, y, z, w] = q;
  const x2 = x + x, y2 = y + y, z2 = z + z;
  const xx = x * x2, xy = x * y2, xz = x * z2;
  const yy = y * y2, yz = y * z2, zz = z * z2;
  const wx = w * x2, wy = w * y2, wz = w * z2;
  const m = identityMat4();
  m[0] = 1 - (yy + zz); m[1] = xy + wz;       m[2] = xz - wy;
  m[4] = xy - wz;       m[5] = 1 - (xx + zz); m[6] = yz + wx;
  m[8] = xz + wy;       m[9] = yz - wx;       m[10] = 1 - (xx + yy);
  return m;
}

function sliceBufferView(buffers: Uint8Array[], views: GltfJson['bufferViews'], index: number): Uint8Array {
  const v = views?.[index];
  if (!v) throw new GltfParseError('corrupt', `bufferView ${index} missing`);
  const bufIndex = v.buffer ?? 0;
  const buf = buffers[bufIndex];
  if (!buf) throw new GltfParseError('corrupt', `buffer ${bufIndex} missing`);
  const off = v.byteOffset ?? 0;
  const len = v.byteLength ?? 0;
  if (off < 0 || len < 0 || off + len > buf.byteLength) {
    throw new GltfParseError('corrupt', `bufferView ${index} range [${off}, ${off + len}) overruns buffer (${buf.byteLength})`);
  }
  return buf.subarray(off, off + len);
}

function decodeDataUri(uri: string): Uint8Array {
  const comma = uri.indexOf(',');
  if (!uri.startsWith('data:') || comma < 0) throw new GltfParseError('corrupt', 'bad data URI');
  const meta = uri.slice(5, comma);
  const body = uri.slice(comma + 1);
  if (meta.includes('base64')) {
    const bin = atob(body);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  throw new GltfParseError('unsupported_feature', 'non-base64 data URI');
}
