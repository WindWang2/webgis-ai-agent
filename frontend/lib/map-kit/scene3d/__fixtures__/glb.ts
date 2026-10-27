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
  /** 交错布局：POSITION/NORMAL 共享 byteStride=24 的 bufferView（glTF 交错
   *  顶点属性常见形态 —— G3DV2-B 严格解析回归位）。 */
  interleave?: boolean;
  /** sparse POSITION：base 全零 + sparse indices/values 覆写前 k 个顶点
   *  （G3DV2-B sparse accessor 回归位；k ≤ 顶点数）。 */
  sparsePositions?: number;
  /** 篡改缝：把第 j 个 sparse index 改成越界值。 */
  corruptSparseIndex?: number;
  /** NORMAL 用归一化整型（normalized:true；5120→/127、5122→/32767）。 */
  normalsComponent?: 5120 | 5122;
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

  // 归一化整型 NORMAL（5120→/127、5122→/32767；首顶点取 -1 分量走负满幅）
  const normalsInt = opts.normalsComponent;
  const normArityBytes = normalsInt === 5120 ? 1 : normalsInt === 5122 ? 2 : 4;
  const encodeNormal = (v: number): number => {
    if (normalsInt === 5120) return Math.max(-127, Math.min(127, Math.round(v * 127)));
    if (normalsInt === 5122) return Math.max(-32767, Math.min(32767, Math.round(v * 32767)));
    return v;
  };

  // sparse POSITION：base 全零 + 前 k 顶点真值经 indices/values 覆写。
  const sparseK = Math.max(0, Math.min(opts.sparsePositions ?? 0, positions.length / 3));
  const spIdx = new Uint16Array(sparseK);
  const spVal = new Float32Array(sparseK * 3);
  for (let i = 0; i < sparseK; i++) {
    spIdx[i] = typeof opts.corruptSparseIndex === 'number' && i === 0
      ? opts.corruptSparseIndex
      : i;
    spVal[i * 3] = positions[i * 3];
    spVal[i * 3 + 1] = positions[i * 3 + 1];
    spVal[i * 3 + 2] = positions[i * 3 + 2];
  }
  const posBase = sparseK > 0 ? new Float32Array(positions.length) : positions;

  const idxBytes = new Uint8Array(indices.buffer);
  const binParts: { bytes: Uint8Array }[] = [];
  const views: { buffer: number; byteOffset: number; byteLength: number; byteStride?: number }[] = [];
  const pushPart = (bytes: Uint8Array, stride?: number): number => {
    const viewIndex = views.length;
    const byteOffset = binParts.reduce((n, p) => n + align4(p.bytes.length), 0);
    binParts.push({ bytes });
    views.push({ buffer: 0, byteOffset, byteLength: bytes.length, ...(stride !== undefined ? { byteStride: stride } : {}) });
    return viewIndex;
  };

  const accessors: Record<string, unknown>[] = [];
  let posAccessor: Record<string, unknown>;
  let normAccessor: Record<string, unknown>;

  if (opts.interleave) {
    // 交错：每顶点记录 [px,py,pz,nx,ny,nz]（24B stride）
    const vertexCount = positions.length / 3;
    const record = new Float32Array(vertexCount * 6);
    for (let v = 0; v < vertexCount; v++) {
      record[v * 6 + 0] = positions[v * 3];
      record[v * 6 + 1] = positions[v * 3 + 1];
      record[v * 6 + 2] = positions[v * 3 + 2];
      record[v * 6 + 3] = normals[v * 3];
      record[v * 6 + 4] = normals[v * 3 + 1];
      record[v * 6 + 5] = normals[v * 3 + 2];
    }
    const view = pushPart(new Uint8Array(record.buffer), 24);
    posAccessor = { bufferView: view, byteOffset: 0, componentType: 5126, count: vertexCount, type: 'VEC3' };
    normAccessor = { bufferView: view, byteOffset: 12, componentType: 5126, count: vertexCount, type: 'VEC3' };
  } else {
    const posViewIdx = pushPart(new Uint8Array(posBase.buffer));
    posAccessor = {
      bufferView: posViewIdx,
      componentType: 5126,
      count: positions.length / 3,
      type: 'VEC3',
      ...(sparseK > 0
        ? {
            sparse: {
              count: sparseK,
              indices: { bufferView: -1, componentType: 5123 }, // 占位，BIN 排布后回填
              values: { bufferView: -1 },
            },
          }
        : {}),
    };
    if (normalsInt) {
      const encoded = new (normalsInt === 5120 ? Int8Array : Int16Array)(normals.length);
      for (let i = 0; i < normals.length; i++) encoded[i] = encodeNormal(normals[i]);
      const view = pushPart(new Uint8Array(encoded.buffer, encoded.byteOffset, encoded.byteLength));
      normAccessor = { bufferView: view, componentType: normalsInt, count: normals.length / 3, type: 'VEC3', normalized: true };
    } else {
      const view = pushPart(new Uint8Array(normals.buffer));
      normAccessor = { bufferView: view, componentType: 5126, count: normals.length / 3, type: 'VEC3' };
    }
  }

  accessors.push(posAccessor, normAccessor);
  if (indexed) {
    const view = pushPart(idxBytes);
    accessors.push({ bufferView: view, componentType: 5125, count: indices.length, type: 'SCALAR' });
  }
  if (sparseK > 0) {
    const sparse = (posAccessor as { sparse: { indices: { bufferView: number }; values: { bufferView: number } } }).sparse;
    sparse.indices.bufferView = pushPart(new Uint8Array(spIdx.buffer));
    sparse.values.bufferView = pushPart(new Uint8Array(spVal.buffer));
  }

  if (opts.corruptBufferViewLength?.viewIndex !== undefined && views[opts.corruptBufferViewLength.viewIndex]) {
    views[opts.corruptBufferViewLength.viewIndex].byteLength += opts.corruptBufferViewLength.delta;
  }

  const binLength = binParts.reduce((n, p) => n + align4(p.bytes.length), 0);
  const bin = new Uint8Array(binLength);
  let off = 0;
  for (const p of binParts) {
    bin.set(p.bytes, off);
    off += align4(p.bytes.length);
  }

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
    accessors,
    bufferViews: views,
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
  /** 子 tile 省略 refine（继承性回归位：应继承根的 refine）。 */
  omitChildRefine?: boolean;
  /** 篡改缝：根 refine 写非法值（typed 拒绝回归位）。 */
  invalidRootRefine?: string;
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
      // OGC 3D Tiles region：经纬弧度（104.05°→1.81886rad 等）+ 米高程。
      boundingVolume: { region: [
        (104.05 * Math.PI) / 180, (30.66 * Math.PI) / 180,
        (104.07 * Math.PI) / 180, (30.68 * Math.PI) / 180, 0, 200,
      ] },
      geometricError: 256,
      ...(opts.invalidRootRefine !== undefined
        ? { refine: opts.invalidRootRefine }
        : { refine: opts.refine ?? 'REPLACE' }),
      content: { uri: `root.${opts.contentExt ?? 'glb'}` },
      children: [
        {
          boundingVolume: { box: [0, 0, 50, 50, 0, 0, 0, 50, 0, 0, 0, 50] },
          geometricError: 0,
          ...(opts.omitChildRefine ? {} : { refine: opts.childRefine ?? 'REPLACE' }),
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
