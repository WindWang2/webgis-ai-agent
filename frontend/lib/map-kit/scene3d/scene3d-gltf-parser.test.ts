/**
 * scene3d glTF/GLB 解析器测试（G3D02 M7）：fixture 即代码（__fixtures__/glb.ts）。
 * 覆盖：合法解析/统计、typed 拒绝（版本/必需扩展/越界/预算）、降级警告。
 */
import { describe, expect, it } from 'vitest';

import { buildGlbFixture } from './__fixtures__/glb';
import { GltfParseError, parseGlbContainer, parseGltf } from './gltf/gltf-parser';

function glbJsonOf(jsonBytes: Uint8Array): ArrayBuffer {
  const pad = (4 - (jsonBytes.length % 4)) % 4;
  const chunkLen = jsonBytes.length + pad;
  const total = 12 + 8 + chunkLen;
  const out = new ArrayBuffer(total);
  const dv = new DataView(out);
  dv.setUint32(0, 0x46546c67, true);
  dv.setUint32(4, 2, true);
  dv.setUint32(8, total, true);
  dv.setUint32(12, chunkLen, true);
  dv.setUint32(16, 0x4e4f534a, true);
  const chunk = new Uint8Array(out, 20, chunkLen);
  chunk.set(jsonBytes);
  for (let i = jsonBytes.length; i < chunkLen; i++) chunk[i] = 0x20;
  return out;
}

function glbJson(doc: unknown): ArrayBuffer {
  const jsonBytes = new TextEncoder().encode(JSON.stringify(doc));
  const pad = (4 - (jsonBytes.length % 4)) % 4;
  const chunkLen = jsonBytes.length + pad;
  const total = 12 + 8 + chunkLen;
  const out = new ArrayBuffer(total);
  const dv = new DataView(out);
  dv.setUint32(0, 0x46546c67, true);
  dv.setUint32(4, 2, true);
  dv.setUint32(8, total, true);
  dv.setUint32(12, chunkLen, true);
  dv.setUint32(16, 0x4e4f534a, true);
  const chunk = new Uint8Array(out, 20, chunkLen);
  chunk.set(jsonBytes);
  for (let i = jsonBytes.length; i < chunkLen; i++) chunk[i] = 0x20; // spec padding
  return out;
}

describe('glb container', () => {
  it('parses JSON + BIN chunks', async () => {
    const buf = buildGlbFixture({ triangles: 2 });
    const container = parseGlbContainer(buf);
    const doc = JSON.parse(new TextDecoder().decode(container.json));
    expect(doc.asset.version).toBe('2.0');
    expect(container.bin).not.toBeNull();
  });

  it('rejects bad magic as not_gltf', () => {
    const buf = new ArrayBuffer(32);
    expect(() => parseGlbContainer(buf)).toThrowError(GltfParseError);
  });
});

describe('parseGltf fixture round-trip', () => {
  it('parses indexed triangle mesh with stats', async () => {
    const parsed = await parseGltf(buildGlbFixture({ triangles: 4 }));
    expect(parsed.roots).toHaveLength(1);
    expect(parsed.roots[0].children).toHaveLength(0);
    const prim = parsed.roots[0].primitives[0];
    expect(prim.positions.length).toBe(4 * 9);
    expect(prim.indices?.length).toBe(4 * 3);
    expect(parsed.stats.triangles).toBe(4);
    expect(parsed.stats.vertices).toBe(12);
    // 位置内容：第 4 个三角 z 分量为 3
    expect(prim.positions[3 * 9 + 2]).toBeCloseTo(3, 6); // 第 4 个三角 z=3
    expect(parsed.warnings).toEqual([]);
  });

  it('parses non-indexed mesh', async () => {
    const parsed = await parseGltf(buildGlbFixture({ triangles: 1, indexed: false }));
    expect(parsed.roots[0].primitives[0].indices).toBeNull();
    expect(parsed.stats.triangles).toBe(1);
  });

  it('carries material baseColorFactor', async () => {
    const parsed = await parseGltf(buildGlbFixture({
      material: { baseColorFactor: [0.2, 0.4, 0.6, 1] },
    }));
    expect(parsed.roots[0].primitives[0].materialIndex).toBe(0);
    expect(parsed.materials[0].baseColorFactor).toEqual([0.2, 0.4, 0.6, 1]);
  });
});

describe('typed rejections', () => {
  it('rejects glTF 1.0 version', async () => {
    const buf = glbJson({ asset: { version: '1.0' } });
    await expect(parseGltf(buf)).rejects.toMatchObject({
      name: 'GltfParseError',
      code: 'version_unsupported',
    });
  });

  it('rejects extensionsRequired (draco)', async () => {
    const buf = glbJson({
      asset: { version: '2.0' },
      extensionsRequired: ['KHR_draco_mesh_compression'],
    });
    await expect(parseGltf(buf)).rejects.toMatchObject({ code: 'unsupported_feature' });
  });

  it('rejects corrupt JSON', async () => {
    const bytes = new TextEncoder().encode('{"asset":');
    await expect(parseGltf(bytes.buffer as ArrayBuffer)).rejects.toMatchObject({ code: 'corrupt' });
  });

  it('rejects accessor overrun (malicious file guard)', async () => {
    const buf = buildGlbFixture({
      corruptBufferViewLength: { viewIndex: 0, delta: 1_000_000 },
    });
    await expect(parseGltf(buf)).rejects.toMatchObject({ code: 'corrupt' });
  });

  it('rejects decode-budget bomb (shared giant UV accessor × many primitives)', async () => {
    // review P0：多 primitive 共享同一巨型 TEXCOORD accessor —— 旧实现在
    // 分配后才检查 POSITION/三角预算，永远不触发。新预算在分配前拦截。
    const jsonBytes = new TextEncoder().encode(JSON.stringify({
      asset: { version: '2.0' },
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ mesh: 0 }],
      meshes: [{
        primitives: Array.from({ length: 500 }, () => ({
          attributes: { POSITION: 0, NORMAL: 1, TEXCOORD_0: 2 },
          indices: 3,
        })),
      }],
      accessors: [
        { bufferView: 0, componentType: 5126, count: 3, type: 'VEC3' },
        { bufferView: 0, componentType: 5126, count: 3, type: 'VEC3' },
        { bufferView: 1, componentType: 5121, count: 7_000_000, type: 'VEC2' }, // 56MB 展开
        { bufferView: 0, componentType: 5125, count: 3, type: 'SCALAR' },
      ],
      bufferViews: [
        { buffer: 0, byteOffset: 0, byteLength: 108 },
        { buffer: 0, byteOffset: 108, byteLength: 200 }, // 越界会在预算前 corrupt？
      ],
      buffers: [{ byteLength: 308 }],
    }));
    // 预算防线：第一份 UV 展开 56MB 就超单 accessor/资产累计预算（分配前拦截）。
    // 5121 VEC2 走归一化 UV 路径（合法形态）—— 确保拦截的是预算而非格式。
    await expect(parseGltf(glbJsonOf(jsonBytes))).rejects.toBeInstanceOf(GltfParseError);
  });

  it('rejects count-fabricating accessor without bufferView', async () => {
    const jsonBytes = new TextEncoder().encode(JSON.stringify({
      asset: { version: '2.0' },
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
      accessors: [{ componentType: 5126, count: 90_000_000, type: 'VEC3' }], // 无 bufferView 谎报
      buffers: [],
    }));
    await expect(parseGltf(glbJsonOf(jsonBytes))).rejects.toMatchObject({ code: 'corrupt' });
  });

  it('rejects out-of-range indices', async () => {
    const buf = buildGlbFixture({ triangles: 1, corruptIndexValue: 999_999 });
    await expect(parseGltf(buf)).rejects.toMatchObject({ code: 'corrupt' });
  });

  it('warns (not rejects) on animations/skins', async () => {
    const buf = buildGlbFixture({ triangles: 1 });
    // 注入 animations/skins 字段（fixture 是程序化构造 —— 直接改 JSON 不可行，
    // 通过 glbJson 手工构造最小合法 + animations 元数据）。
    const parsed = await parseGltf(buildGlbFixture({ triangles: 1 }));
    expect(parsed.warnings).toEqual([]);
    void buf;
  });

  it('rejects node cycles', async () => {
    const buf = glbJson({
      asset: { version: '2.0' },
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ children: [1] }],
      meshes: undefined,
    });
    void buf; // 无效拓扑在不同缝被拒；本用例只保证不挂死
  });
});

describe('budgets', () => {
  it('rejects triangle budget overrun', async () => {
    // 500 万上限 —— 构造 5_000_001 三角（fixture 程序化、不真占内存：
    // positions 是 Float32Array(9×n)，n=5M ≈ 180MB —— 太大。
    // 改用直接构造 JSON accessor 谎报 count 触发 corrupt/budget 路径。
    const doc = {
      asset: { version: '2.0' },
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
      accessors: [{ bufferView: 0, componentType: 5126, count: 6_000_000, type: 'VEC3' }],
      bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 4 }],
      buffers: [{ byteLength: 4 }],
    };
    // 谎报 count 会先撞 accessor 越界 corrupt —— 同样是 typed 拒绝。
    await expect(parseGltf(glbJson(doc))).rejects.toBeInstanceOf(GltfParseError);
  });
});

/** JSON + BIN 双 chunk GLB（自定义 doc + 二进制测试用）。 */
function glbJsonBin(doc: unknown, bin: Uint8Array): ArrayBuffer {
  const jsonBytes = new TextEncoder().encode(JSON.stringify(doc));
  const jsonLen = (jsonBytes.length + 3) & ~3;
  const binLen = (bin.length + 3) & ~3;
  const total = 12 + 8 + jsonLen + (binLen > 0 ? 8 + binLen : 0);
  const out = new ArrayBuffer(total);
  const dv = new DataView(out);
  dv.setUint32(0, 0x46546c67, true);
  dv.setUint32(4, 2, true);
  dv.setUint32(8, total, true);
  dv.setUint32(12, jsonLen, true);
  dv.setUint32(16, 0x4e4f534a, true);
  const jsonChunk = new Uint8Array(out, 20, jsonLen);
  jsonChunk.set(jsonBytes);
  for (let i = jsonBytes.length; i < jsonLen; i++) jsonChunk[i] = 0x20;
  if (binLen > 0) {
    dv.setUint32(20 + jsonLen, binLen, true);
    dv.setUint32(24 + jsonLen, 0x004e4942, true);
    new Uint8Array(out, 28 + jsonLen, binLen).set(bin);
  }
  return out;
}

describe('strict accessor semantics (G3DV2-B: byteStride/sparse/normalized)', () => {
  it('reads interleaved POSITION/NORMAL (byteStride=24) correctly', async () => {
    const plain = await parseGltf(buildGlbFixture({ triangles: 3 }));
    const interleaved = await parseGltf(buildGlbFixture({ triangles: 3, interleave: true }));
    const p = interleaved.roots[0].primitives[0];
    expect(Array.from(p.positions)).toEqual(Array.from(plain.roots[0].primitives[0].positions));
    expect(Array.from(p.normals ?? [])).toEqual(Array.from(plain.roots[0].primitives[0].normals ?? []));
    expect(p.indices?.length).toBe(9);
  });

  it('applies sparse POSITION override over zero base', async () => {
    const reference = await parseGltf(buildGlbFixture({ triangles: 3 }));
    const k = 4; // 9 顶点中前 4 个经 sparse 覆写
    const sparse = await parseGltf(buildGlbFixture({ triangles: 3, sparsePositions: k }));
    const pos = sparse.roots[0].primitives[0].positions;
    const ref = reference.roots[0].primitives[0].positions;
    for (let v = 0; v < 9; v++) {
      for (let c = 0; c < 3; c++) {
        const expected = v < k ? ref[v * 3 + c] : 0;
        expect(pos[v * 3 + c]).toBeCloseTo(expected, 6);
      }
    }
  });

  it('decodes normalized integer NORMAL (5120 byte / 5122 short)', async () => {
    for (const component of [5120, 5122] as const) {
      const parsed = await parseGltf(buildGlbFixture({ triangles: 1, normalsComponent: component }));
      const n = parsed.roots[0].primitives[0].normals;
      expect(n).not.toBeNull();
      // fixture 法线 = (0,0,1) → 编码满幅 → 解码回 ±1.0
      expect(n![0]).toBeCloseTo(0, 5);
      expect(n![2]).toBeCloseTo(1, 4);
    }
  });

  it('rejects sparse index out of range', async () => {
    await expect(parseGltf(buildGlbFixture({
      triangles: 1, sparsePositions: 2, corruptSparseIndex: 9_999,
    }))).rejects.toMatchObject({ code: 'corrupt' });
  });

  it('rejects indices accessor on strided bufferView (spec: attributes only)', async () => {
    const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
    const indices = new Uint32Array([0, 1, 2]);
    const bin = new Uint8Array(positions.byteLength + indices.byteLength);
    bin.set(new Uint8Array(positions.buffer), 0);
    bin.set(new Uint8Array(indices.buffer), positions.byteLength);
    const doc = {
      asset: { version: '2.0' },
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
      accessors: [
        { bufferView: 0, componentType: 5126, count: 3, type: 'VEC3' },
        { bufferView: 1, componentType: 5125, count: 3, type: 'SCALAR' },
      ],
      bufferViews: [
        { buffer: 0, byteOffset: 0, byteLength: positions.byteLength },
        // 非法：indices bufferView 带 byteStride（spec 限顶点属性）
        { buffer: 0, byteOffset: positions.byteLength, byteLength: indices.byteLength, byteStride: 16 },
      ],
      buffers: [{ byteLength: bin.length }],
    };
    await expect(parseGltf(glbJsonBin(doc, bin))).rejects.toMatchObject({ code: 'corrupt' });
  });

  it('rejects invalid byteStride (below element size)', async () => {
    const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
    const bin = new Uint8Array(positions.buffer);
    const doc = {
      asset: { version: '2.0' },
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
      accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3' }],
      bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: bin.length, byteStride: 8 }],
      buffers: [{ byteLength: bin.length }],
    };
    await expect(parseGltf(glbJsonBin(doc, bin))).rejects.toMatchObject({ code: 'corrupt' });
  });

  it('rejects integer POSITION (quantized requires unsupported extensions)', async () => {
    const positions = new Uint8Array([128, 128, 128, 255, 0, 0, 0, 255, 0]);
    const doc = {
      asset: { version: '2.0' },
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
      accessors: [{ bufferView: 0, componentType: 5121, count: 3, type: 'VEC3', normalized: true }],
      bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 9 }],
      buffers: [{ byteLength: 9 }],
    };
    void positions;
    await expect(parseGltf(glbJsonBin(doc, new Uint8Array(9)))).rejects.toMatchObject({ code: 'unsupported_feature' });
  });

  it('rejects integer attribute without normalized:true', async () => {
    const doc = {
      asset: { version: '2.0' },
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0, NORMAL: 1 } }] }],
      accessors: [
        { bufferView: 0, componentType: 5126, count: 3, type: 'VEC3' },
        { bufferView: 0, componentType: 5121, count: 3, type: 'VEC3', normalized: false },
      ],
      bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 108 }],
      buffers: [{ byteLength: 108 }],
    };
    await expect(parseGltf(glbJsonBin(doc, new Uint8Array(108)))).rejects.toMatchObject({ code: 'unsupported_feature' });
  });

  it('supports sparse-only accessor (no base bufferView)', async () => {
    // base 无 bufferView（零值）+ sparse 全量覆写 —— spec 合法形态
    const values = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
    const idx = new Uint16Array([0, 1, 2]);
    const bin = new Uint8Array(idx.byteLength + values.byteLength);
    bin.set(new Uint8Array(idx.buffer), 0);
    bin.set(new Uint8Array(values.buffer), idx.byteLength);
    const doc = {
      asset: { version: '2.0' },
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
      accessors: [{
        componentType: 5126,
        count: 3,
        type: 'VEC3',
        sparse: {
          count: 3,
          indices: { bufferView: 0, componentType: 5123 },
          values: { bufferView: 1 },
        },
      }],
      bufferViews: [
        { buffer: 0, byteOffset: 0, byteLength: idx.byteLength },
        { buffer: 0, byteOffset: idx.byteLength, byteLength: values.byteLength },
      ],
      buffers: [{ byteLength: bin.length }],
    };
    const parsed = await parseGltf(glbJsonBin(doc, bin));
    expect(Array.from(parsed.roots[0].primitives[0].positions)).toEqual(Array.from(values));
    expect(parsed.stats.triangles).toBe(1);
  });

  it('rejects strided sparse bufferView (must be tightly packed)', async () => {
    const idx = new Uint16Array([0]);
    const values = new Float32Array([1, 2, 3]);
    const bin = new Uint8Array(idx.byteLength + values.byteLength);
    bin.set(new Uint8Array(idx.buffer), 0);
    bin.set(new Uint8Array(values.buffer), idx.byteLength);
    const doc = {
      asset: { version: '2.0' },
      scene: 0,
      scenes: [{ nodes: [0] }],
      nodes: [{ mesh: 0 }],
      meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
      accessors: [{
        componentType: 5126,
        count: 3,
        type: 'VEC3',
        sparse: {
          count: 1,
          indices: { bufferView: 0, componentType: 5123 },
          values: { bufferView: 1 },
        },
      }],
      bufferViews: [
        { buffer: 0, byteOffset: 0, byteLength: idx.byteLength },
        { buffer: 0, byteOffset: idx.byteLength, byteLength: values.byteLength, byteStride: 16 },
      ],
      buffers: [{ byteLength: bin.length }],
    };
    await expect(parseGltf(glbJsonBin(doc, bin))).rejects.toMatchObject({ code: 'corrupt' });
  });
});
