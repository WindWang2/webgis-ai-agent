/**
 * scene3d 测试 fixture —— 程序化 GLB 构造器（G3D02 M7）。
 *
 * 生成最小合法 glTF 2.0 GLB（三角面网格 + 可选材质/顶点色语义位），供
 * 解析器/管理器测试消费。零外部资产依赖 —— fixture 即代码。
 */

export interface GlbFixtureOptions {
  /** 三角形数（生成立方体重复铺开；默认 1 = 单三角）。 */
  triangles?: number;
  /** 顶点模式 cover：三角形在 East-Up 平面内包住原点（拾取旅程用 ——
   *  屏心射线命中内部而非边界顶点）。 */
  coverOrigin?: boolean;
  material?: {
    baseColorFactor?: [number, number, number, number];
    doubleSided?: boolean;
  } | null;
  /** 带 indices buffer（默认 true）。 */
  indexed?: boolean;
  /** 篡改缝（负例测试）：在指定 bufferView 的 byteLength 上加偏移。 */
  corruptBufferViewLength?: { viewIndex: number; delta: number };
  /** 篡改缝：indices 越界值。 */
  corruptIndexValue?: number;
}

function align4(n: number): number {
  return (n + 3) & ~3;
}

export function buildGlbFixture(opts: GlbFixtureOptions = {}): ArrayBuffer {
  const triangles = Math.max(1, opts.triangles ?? 1);
  const positions = new Float32Array(triangles * 9);
  for (let t = 0; t < triangles; t++) {
    const o = t * 9;
    if (opts.coverOrigin) {
      // East-Up 平面内包住原点的三角（glTF y-up：y = 高度）。
      positions[o + 0] = -1; positions[o + 1] = -1; positions[o + 2] = t;
      positions[o + 3] = 2; positions[o + 4] = 1; positions[o + 5] = t;
      positions[o + 6] = -1; positions[o + 7] = 3; positions[o + 8] = t;
    } else {
      // 每个三角 z 提升 1：可预测有界几何
      positions[o + 0] = 0; positions[o + 1] = 0; positions[o + 2] = t;
      positions[o + 3] = 1; positions[o + 4] = 0; positions[o + 5] = t;
      positions[o + 6] = 0; positions[o + 7] = 1; positions[o + 8] = t;
    }
  }
  const normals = new Float32Array(triangles * 9);
  for (let i = 0; i < normals.length; i += 3) {
    normals[i] = 0; normals[i + 1] = 0; normals[i + 2] = 1;
  }
  const indexed = opts.indexed !== false;
  const indices = new Uint32Array(triangles * 3);
  for (let t = 0; t < triangles; t++) {
    const o = t * 3;
    indices[o] = t * 3;
    indices[o + 1] = t * 3 + 1;
    indices[o + 2] = t * 3 + 2;
  }
  if (typeof opts.corruptIndexValue === 'number' && indices.length > 0) {
    indices[indices.length - 1] = opts.corruptIndexValue;
  }

  // BIN chunk：positions | normals | indices（4 字节对齐）
  const posBytes = new Uint8Array(positions.buffer);
  const normBytes = new Uint8Array(normals.buffer);
  const idxBytes = new Uint8Array(indices.buffer);
  const binLength = align4(posBytes.length) + align4(normBytes.length)
    + (indexed ? align4(idxBytes.length) : 0);
  const bin = new Uint8Array(binLength);
  let off = 0;
  bin.set(posBytes, off); off += align4(posBytes.length);
  bin.set(normBytes, off); off += align4(normBytes.length);
  if (indexed) bin.set(idxBytes, off);

  const posView = { buffer: 0, byteOffset: 0, byteLength: posBytes.length };
  const normView = { buffer: 0, byteOffset: align4(posBytes.length), byteLength: normBytes.length };
  const idxView = { buffer: 0, byteOffset: align4(posBytes.length) + align4(normBytes.length), byteLength: idxBytes.length };
  if (opts.corruptBufferViewLength?.viewIndex === 0) posView.byteLength += opts.corruptBufferViewLength.delta;

  const materialDef = opts.material === null
    ? undefined
    : {
        ...(opts.material?.baseColorFactor
          ? { pbrMetallicRoughness: { baseColorFactor: opts.material.baseColorFactor } }
          : {}),
        ...(opts.material?.doubleSided ? { doubleSided: true } : {}),
      };

  const json = {
    asset: { version: '2.0' },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0, name: 'fixture-mesh' }],
    meshes: [{
      primitives: [{
        attributes: { POSITION: 0, NORMAL: 1 },
        ...(indexed ? { indices: 2 } : {}),
        ...(materialDef ? { material: 0 } : {}),
      }],
    }],
    ...(materialDef ? { materials: [materialDef] } : {}),
    accessors: [
      { bufferView: 0, componentType: 5126, count: positions.length / 3, type: 'VEC3' },
      { bufferView: 1, componentType: 5126, count: normals.length / 3, type: 'VEC3' },
      ...(indexed
        ? [{ bufferView: 2, componentType: 5125, count: indices.length, type: 'SCALAR' }]
        : []),
    ],
    bufferViews: [posView, normView, ...(indexed ? [idxView] : [])],
    buffers: [{ byteLength: binLength }],
  };
  const jsonBytes = new TextEncoder().encode(JSON.stringify(json));

  const total = 12 + 8 + align4(jsonBytes.length) + (binLength > 0 ? 8 + binLength : 0);
  const out = new ArrayBuffer(total);
  const dv = new DataView(out);
  dv.setUint32(0, 0x46546c67, true); // 'glTF'
  dv.setUint32(4, 2, true);
  dv.setUint32(8, total, true);
  let o = 12;
  const jsonChunkLen = align4(jsonBytes.length);
  dv.setUint32(o, jsonChunkLen, true);
  dv.setUint32(o + 4, 0x4e4f534a, true); // 'JSON'
  const jsonChunk = new Uint8Array(out, o + 8, jsonChunkLen);
  jsonChunk.set(jsonBytes);
  // GLB 规范：JSON chunk 以空格 (0x20) 填充到 4 字节对齐。
  for (let i = jsonBytes.length; i < jsonChunkLen; i++) jsonChunk[i] = 0x20;
  o += 8 + align4(jsonBytes.length);
  if (binLength > 0) {
    dv.setUint32(o, binLength, true);
    dv.setUint32(o + 4, 0x004e4942, true); // 'BIN'
    new Uint8Array(out, o + 8, binLength).set(bin);
  }
  return out;
}

/** 最小 3D Tiles 1.1 tileset 文档（region 根 + 2 个 glb 子 tile）。 */
export function buildTilesetFixture(opts: {
  rootGeometricError?: number;
  refine?: 'REPLACE' | 'ADD';
  childRefine?: 'REPLACE' | 'ADD';
  contentExt?: string;
} = {}): Record<string, unknown> {
  return {
    asset: { version: '1.1' },
    geometricError: opts.rootGeometricError ?? 512,
    root: {
      transform: [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
      ],
      boundingVolume: { region: [104.05, 30.66, 104.07, 30.68, 0, 200] },
      geometricError: 256,
      refine: opts.refine ?? 'REPLACE',
      content: { uri: `root.${opts.contentExt ?? 'glb'}` },
      children: [
        {
          boundingVolume: { box: [0, 0, 50, 50, 0, 0, 0, 50, 0, 0, 0, 50] },
          geometricError: 0,
          refine: opts.childRefine ?? 'REPLACE',
          content: { uri: `child-a.${opts.contentExt ?? 'glb'}` },
        },
        {
          boundingVolume: { box: [200, 200, 50, 50, 0, 0, 0, 50, 0, 0, 0, 50] },
          geometricError: 0,
          refine: 'REPLACE',
          content: { uri: `child-b.${opts.contentExt ?? 'glb'}` },
        },
      ],
    },
  };
}
