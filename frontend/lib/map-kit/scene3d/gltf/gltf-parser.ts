/**
 * scene3d/gltf/gltf-parser — glTF 2.0 / GLB 受控子集解析器（G3D02 M3）。
 *
 * 纯函数、零 THREE/WebGL 依赖（jsdom 可测）。产出普通类型化数组层级
 * （ParsedGltf），由 build.ts 投影为 THREE 对象。
 *
 * 能力边界（typed 拒绝/降级，绝不静默）：
 * - 拒绝（GltfParseError.unsupported_feature）：extensionsRequired 非空
 *   （Draco/量化/KHR_materials_* 等）、外部 image URI、多 primitive 之外的
 *   扩展拓扑、裸整型 POSITION/未归一化整型属性。
 * - 降级（warnings，渲染继续）：animations/skins/morph targets 忽略
 *   （静态渲染是合法表达）；缺 NORMAL 用 flat shading；可选扩展忽略。
 * - accessor 严格语义（G3DV2-B）：bufferView.byteStride 交错属性按步进
 *   正确读取（紧密打包为特例）；sparse accessor 按 spec 应用 indices/values
 *   覆写；normalized 整型全词表（5120/5121/5122/5123）；indices/sparse
 *   bufferView 必须紧密打包（spec 语义，typed 拒绝）。
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
  stats: ParsedGltfStats;
  warnings: string[];
}

/** 资源账目（G3DV2-B M3：跨资产累计预算的计量输入）。 */
export interface ParsedGltfStats {
  triangles: number;
  vertices: number;
  meshes: number;
  /** 几何 buffer 字节（position/normal/uv/index 展开后总和）。 */
  geometryBytes: number;
  /** 内嵌纹理字节。 */
  textureBytes: number;
  /** 材质数。 */
  materialCount: number;
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

interface JsonSparseBlock {
  count: number;
  indices: { bufferView: number; byteOffset?: number; componentType: number };
  values: { bufferView: number; byteOffset?: number };
}

interface JsonAccessor {
  bufferView?: number;
  byteOffset?: number;
  componentType: number;
  count: number;
  type: string;
  normalized?: boolean;
  sparse?: JsonSparseBlock;
}

const COMPONENT_BYTES: Record<number, number> = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 };
const TYPE_ARITY: Record<string, number> = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT4: 16 };
/** 归一化整型除数（spec §5.14.2：v/max；有符号负满幅 clamp 到 -1.0）。 */
const NORMALIZED_DIVISORS: Record<number, number> = { 5120: 127, 5121: 255, 5122: 32767, 5123: 65535 };

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
  let geometryBytes = 0;
  /** 累计解码字节（review P0：分配前预算 —— 多 primitive 共享巨型 accessor
   *  与谎报 count 的两类放大炸弹在此拦截）。 */
  let decodedBytes = 0;

  /** 单分量读取（交错/紧密共用；归一化按 spec 除数换算）。 */
  const readComponent = (dv: DataView, abs: number, componentType: number, normalized: boolean): number => {
    switch (componentType) {
      case 5126: return dv.getFloat32(abs, true);
      case 5121: {
        const v = dv.getUint8(abs);
        return normalized ? v / NORMALIZED_DIVISORS[5121] : v;
      }
      case 5123: {
        const v = dv.getUint16(abs, true);
        return normalized ? v / NORMALIZED_DIVISORS[5123] : v;
      }
      case 5120: {
        const v = dv.getInt8(abs);
        return normalized ? Math.max(v / NORMALIZED_DIVISORS[5120], -1) : v;
      }
      case 5122: {
        const v = dv.getInt16(abs, true);
        return normalized ? Math.max(v / NORMALIZED_DIVISORS[5122], -1) : v;
      }
      default:
        throw new GltfParseError('unsupported_feature', `componentType ${componentType}`);
    }
  };

  /**
   * 属性 bufferView 步进：byteStride 未定义 = 紧密打包（元素大小即步长）；
   * 定义时必须 ≥ 元素字节数且为分量大小整数倍（spec 语义），否则 typed
   * 拒绝 —— 这是「交错文件静默错读」的根治点。
   */
  const attributeStrideOf = (
    viewIndex: number,
    compBytes: number,
    elemBytes: number,
    accessorIndex: number,
  ): number => {
    const stride = doc.bufferViews?.[viewIndex]?.byteStride;
    if (stride === undefined) return elemBytes;
    if (!Number.isInteger(stride) || stride < elemBytes || stride % compBytes !== 0) {
      throw new GltfParseError(
        'corrupt',
        `bufferView ${viewIndex} byteStride ${stride} invalid for accessor ${accessorIndex} (element ${elemBytes}B, component ${compBytes}B)`,
      );
    }
    return stride;
  };

  /**
   * sparse accessor 覆写（spec §5.14.2.3）：indices/values 必须紧密打包、
   * componentType 词表校验、越界 index typed 拒绝；解码预算先行（防
   * 「base 小 + sparse 巨大」的绕预算形态）。
   */
  const applySparse = (
    acc: JsonAccessor,
    count: number,
    arity: number,
    accessorIndex: number,
    write: (elIdx: number, valDv: DataView, row: number) => void,
  ): void => {
    const sp = acc.sparse;
    if (!sp) return;
    const sparseCount = sp.count;
    if (!Number.isInteger(sparseCount) || sparseCount < 0 || sparseCount > count) {
      throw new GltfParseError('corrupt', `accessor ${accessorIndex} sparse.count ${sparseCount} invalid`);
    }
    if (typeof sp.indices?.bufferView !== 'number' || typeof sp.values?.bufferView !== 'number') {
      throw new GltfParseError('corrupt', `accessor ${accessorIndex} sparse missing bufferView`);
    }
    const idxComp = sp.indices.componentType;
    if (idxComp !== 5121 && idxComp !== 5123 && idxComp !== 5125) {
      throw new GltfParseError('corrupt', `accessor ${accessorIndex} sparse.indices.componentType ${idxComp}`);
    }
    const compBytes = COMPONENT_BYTES[acc.componentType];
    const elemBytes = compBytes * arity;
    const sparseExpanded = sparseCount * 4 + sparseCount * arity * 4;
    if (decodedBytes + sparseExpanded > MAX_DECODED_BYTES_PER_ASSET) {
      throw new GltfParseError(
        'budget_exceeded',
        `accessor ${accessorIndex} sparse decoded ${sparseExpanded}B exceeds budget (${decodedBytes}B used)`,
      );
    }
    decodedBytes += sparseExpanded;
    if (doc.bufferViews?.[sp.indices.bufferView]?.byteStride !== undefined
        || doc.bufferViews?.[sp.values.bufferView]?.byteStride !== undefined) {
      throw new GltfParseError('corrupt', `accessor ${accessorIndex} sparse bufferViews must be tightly packed`);
    }
    const idxView = sliceBufferView(buffers, doc.bufferViews, sp.indices.bufferView);
    const valView = sliceBufferView(buffers, doc.bufferViews, sp.values.bufferView);
    const idxCompBytes = COMPONENT_BYTES[idxComp];
    const idxBase = sp.indices.byteOffset ?? 0;
    const valBase = sp.values.byteOffset ?? 0;
    if (idxBase + sparseCount * idxCompBytes > idxView.byteLength) {
      throw new GltfParseError('corrupt', `accessor ${accessorIndex} sparse indices overrun bufferView`);
    }
    if (valBase + sparseCount * elemBytes > valView.byteLength) {
      throw new GltfParseError('corrupt', `accessor ${accessorIndex} sparse values overrun bufferView`);
    }
    const idxDv = new DataView(idxView.buffer, idxView.byteOffset, idxView.byteLength);
    const valDv = new DataView(valView.buffer, valView.byteOffset, valView.byteLength);
    const readSparseIndex = (j: number): number => {
      const abs = idxBase + j * idxCompBytes;
      if (idxComp === 5125) return idxDv.getUint32(abs, true);
      if (idxComp === 5123) return idxDv.getUint16(abs, true);
      return idxDv.getUint8(abs);
    };
    for (let j = 0; j < sparseCount; j++) {
      const elIdx = readSparseIndex(j);
      if (elIdx >= count) {
        throw new GltfParseError('corrupt', `accessor ${accessorIndex} sparse index ${elIdx} >= count ${count}`);
      }
      write(elIdx, valDv, valBase + j * elemBytes);
    }
  };

  const decodeAccessor = (
    index: number,
    semantic: 'POSITION' | 'NORMAL' | 'TEXCOORD_0',
  ): { typed: Float32Array; normalized: boolean } => {
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
    const elemBytes = compBytes * arity;
    // 展开后（Float32 化）大小 —— 分配前检查（review P0-1；先于语义门，
    // 「预算拦截而非格式拦截」是既有测试锁定的顺序）。
    const expandedBytes = count * arity * 4;
    if (expandedBytes > MAX_DECODED_BYTES_PER_ACCESSOR
        || decodedBytes + expandedBytes > MAX_DECODED_BYTES_PER_ASSET) {
      throw new GltfParseError(
        'budget_exceeded',
        `accessor ${index} decoded size ${expandedBytes}B exceeds budget (asset total ${decodedBytes}B + ${expandedBytes}B > ${MAX_DECODED_BYTES_PER_ASSET}B)`,
      );
    }
    decodedBytes += expandedBytes;
    // 语义门：POSITION 仅 FLOAT（归一化 [-1,1] / 裸整型坐标都不可解释为
    // 几何语义，量化形态必带 extensionsRequired 已在入口拒绝）。
    if (acc.componentType !== 5126) {
      if (semantic === 'POSITION') {
        throw new GltfParseError(
          'unsupported_feature',
          `POSITION componentType ${acc.componentType} requires unsupported quantization extensions`,
        );
      }
      if (acc.normalized !== true) {
        throw new GltfParseError(
          'unsupported_feature',
          `integer ${semantic} accessor without normalized:true (quantized attributes require unsupported extensions)`,
        );
      }
    }
    const normalized = acc.componentType !== 5126 && acc.normalized === true;
    const out = new Float32Array(count * arity);
    if (acc.bufferView !== undefined) {
      const view = sliceBufferView(buffers, doc.bufferViews, acc.bufferView);
      const stride = attributeStrideOf(acc.bufferView, compBytes, elemBytes, index);
      const base = acc.byteOffset ?? 0;
      const span = count === 0 ? 0 : (count - 1) * stride + elemBytes;
      if (base < 0 || base + span > view.byteLength) {
        throw new GltfParseError(
          'corrupt',
          `accessor ${index} range [${base}, ${base + span}) overruns bufferView (${view.byteLength}B, stride ${stride})`,
        );
      }
      const dv = new DataView(view.buffer, view.byteOffset, view.byteLength);
      for (let i = 0; i < count; i++) {
        const row = base + i * stride;
        for (let c = 0; c < arity; c++) {
          out[i * arity + c] = readComponent(dv, row + c * compBytes, acc.componentType, normalized);
        }
      }
    }
    // sparse（base 无 bufferView = sparse-only accessor 合法形态，out 已零初始化）
    applySparse(acc, count, arity, index, (elIdx, valDv, row) => {
      for (let c = 0; c < arity; c++) {
        out[elIdx * arity + c] = readComponent(valDv, row + c * compBytes, acc.componentType, normalized);
      }
    });
    return { typed: out, normalized };
  };

  const decodeIndices = (index: number, vertexCount: number): Uint32Array => {
    const acc = doc.accessors?.[index];
    if (!acc) throw new GltfParseError('corrupt', `indices accessor ${index} missing`);
    if (acc.type !== 'SCALAR') throw new GltfParseError('corrupt', 'indices must be SCALAR');
    const count = acc.count;
    // typed 校验（review R1-P3-5：负/非整 count 会以裸 RangeError 冒泡）。
    if (!Number.isInteger(count) || count < 0) {
      throw new GltfParseError('corrupt', `indices accessor ${index} count invalid`);
    }
    // 索引展开 Uint32 —— 分配前预算（同 P0-1）。
    const expandedBytes = count * 4;
    if (expandedBytes > MAX_DECODED_BYTES_PER_ACCESSOR
        || decodedBytes + expandedBytes > MAX_DECODED_BYTES_PER_ASSET) {
      throw new GltfParseError('budget_exceeded', `indices accessor ${index} exceeds decoded budget`);
    }
    decodedBytes += expandedBytes;
    // 索引必须绑 bufferView：spec 合法的 sparse-only 索引 accessor 在此
    // 保守拒绝（corrupt）—— 零基索引全指向顶点 0 是退化几何，不支持
    // 好过静默错读（review R1-P3-5 / R2 接受的子集边界）。
    const viewIndex = acc.bufferView ?? -1;
    const view = sliceBufferView(buffers, doc.bufferViews, viewIndex);
    // spec：byteStride 仅限顶点属性 bufferView —— 交错索引无法喂 drawElements。
    if (doc.bufferViews?.[viewIndex]?.byteStride !== undefined) {
      throw new GltfParseError('corrupt', `indices accessor ${index} bufferView must be tightly packed`);
    }
    if (acc.normalized === true) {
      throw new GltfParseError('corrupt', `indices accessor ${index} must not be normalized`);
    }
    const base = acc.byteOffset ?? 0;
    if (base < 0) throw new GltfParseError('corrupt', `indices accessor ${index} byteOffset invalid`);
    const out = new Uint32Array(count);
    const dv = new DataView(view.buffer, view.byteOffset, view.byteLength);
    if (acc.componentType === 5125) {
      if (base + count * 4 > view.byteLength) throw new GltfParseError('corrupt', 'indices overrun');
      for (let i = 0; i < count; i++) out[i] = dv.getUint32(base + i * 4, true);
    } else if (acc.componentType === 5123) {
      if (base + count * 2 > view.byteLength) throw new GltfParseError('corrupt', 'indices overrun');
      for (let i = 0; i < count; i++) out[i] = dv.getUint16(base + i * 2, true);
    } else if (acc.componentType === 5121) {
      if (base + count > view.byteLength) throw new GltfParseError('corrupt', 'indices overrun');
      for (let i = 0; i < count; i++) out[i] = dv.getUint8(base + i);
    } else {
      throw new GltfParseError('unsupported_feature', `indices componentType ${acc.componentType}`);
    }
    // sparse 索引（规范允许；values 与 accessor 同 componentType 的无符号标量）
    applySparse(acc, count, 1, index, (elIdx, valDv, row) => {
      if (acc.componentType === 5125) out[elIdx] = valDv.getUint32(row, true);
      else if (acc.componentType === 5123) out[elIdx] = valDv.getUint16(row, true);
      else out[elIdx] = valDv.getUint8(row);
    });
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
        const positions = decodeAccessor(posIdx, 'POSITION').typed;
        const vertices = positions.length / 3;
        totalVertices += vertices;
        if (totalVertices > MAX_VERTICES_PER_ASSET) {
          throw new GltfParseError('budget_exceeded', `vertices > ${MAX_VERTICES_PER_ASSET}`);
        }
        const normIdx = prim.attributes?.NORMAL;
        const normals = typeof normIdx === 'number' ? decodeAccessor(normIdx, 'NORMAL').typed : null;
        const uvIdx = prim.attributes?.TEXCOORD_0;
        const uvs = typeof uvIdx === 'number' ? decodeAccessor(uvIdx, 'TEXCOORD_0').typed : null;
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
        geometryBytes += positions.byteLength + (normals?.byteLength ?? 0)
          + (uvs?.byteLength ?? 0) + (indices?.byteLength ?? 0);
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
    stats: {
      triangles: Math.round(totalTriangles),
      vertices: totalVertices,
      meshes: meshCount,
      geometryBytes,
      textureBytes: textures.reduce((n, t) => n + t.bytes.byteLength, 0),
      materialCount: materials.length,
    },
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
