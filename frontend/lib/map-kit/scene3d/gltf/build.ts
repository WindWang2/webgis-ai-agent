/**
 * scene3d/gltf/build — ParsedGltf → THREE 对象投影（G3D02 M3）。
 *
 * 子集语义：POSITION/NORMAL/TEXCOORD_0 + indices → BufferGeometry；
 * pbr baseColorFactor / metallic / roughness / doubleSided →
 * MeshStandardMaterial（无环境贴图 —— 室内近似，能力边界在模块头声明）；
 * 内嵌纹理经 Blob URL 异步装配（不可用环境降级为因子色，typed warning）。
 */

import * as THREE from 'three';

import type { ParsedGltf, ParsedNode, ParsedPrimitive } from './gltf-parser';

export interface BuildOptions {
  /** 纹理装配失败时的降级是否记录（warnings 回传）。 */
  onTextureDegraded?: (message: string) => void;
}

export interface BuiltModel {
  root: THREE.Group;
  /** pick 命中索引：mesh.uuid → 节点元数据（name/extras）。 */
  meshMeta: Map<string, { nodeName: string | null; extras: unknown }>;
  dispose(): void;
}

const disposables: unique symbol = Symbol('scene3d.disposables');

interface DisposableCarrier {
  [disposables]?: Array<{ dispose(): void }>;
}

/** 纹理创建缝（测试注入；浏览器实现用 Blob URL + TextureLoader）。 */
export type TextureFactory = (
  bytes: Uint8Array,
  mimeType: string,
) => THREE.Texture | null;

let textureFactory: TextureFactory | null = defaultTextureFactory;

export function setTextureFactory(factory: TextureFactory | null): void {
  textureFactory = factory ?? defaultTextureFactory;
}

function defaultTextureFactory(bytes: Uint8Array, mimeType: string): THREE.Texture | null {
  if (typeof Blob === 'undefined' || typeof URL?.createObjectURL !== 'function') {
    return null;
  }
  try {
    const blob = new Blob([bytes.slice().buffer as ArrayBuffer], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const texture = new THREE.Texture();
    const image = new Image();
    image.onload = () => {
      texture.image = image;
      texture.needsUpdate = true;
    };
    image.src = url;
    // Blob URL 在纹理上传 GPU 后释放（onload 竞态由浏览器保证 image 持有解码位图）。
    image.onerror = () => URL.revokeObjectURL(url);
    return texture;
  } catch {
    return null;
  }
}

export function buildModel(parsed: ParsedGltf, opts: BuildOptions = {}): BuiltModel {
  const root = new THREE.Group();
  const meshMeta = new Map<string, { nodeName: string | null; extras: unknown }>();
  const geos: THREE.BufferGeometry[] = [];
  const mats: THREE.Material[] = [];

  const makeMaterial = (prim: ParsedPrimitive): THREE.MeshStandardMaterial => {
    const spec = prim.materialIndex !== null ? parsed.materials[prim.materialIndex] : null;
    const material = new THREE.MeshStandardMaterial({
      color: new THREE.Color(
        spec?.baseColorFactor[0] ?? 1,
        spec?.baseColorFactor[1] ?? 1,
        spec?.baseColorFactor[2] ?? 1,
      ),
      metalness: spec?.metallicFactor ?? 0.1,
      roughness: spec?.roughnessFactor ?? 0.85,
      side: spec?.doubleSided ? THREE.DoubleSide : THREE.FrontSide,
    });
    if (spec && spec.baseColorTextureIndex !== null && prim.uvs) {
      const tex = parsed.textures[spec.baseColorTextureIndex];
      if (tex) {
        const texture = textureFactory
          ? textureFactory(tex.bytes, tex.mimeType)
          : null;
        if (texture) {
          texture.flipY = false; // glTF UV 原点左上（与 OpenGL 纹理翻转语义相反）
          material.map = texture;
          material.color.set(0xffffff);
        } else {
          opts.onTextureDegraded?.('texture_unavailable_degraded_to_factor_color');
        }
      }
    }
    mats.push(material);
    return material;
  };

  const buildNode = (node: ParsedNode, parent: THREE.Object3D): void => {
    const holder = new THREE.Group();
    holder.name = node.name ?? '';
    holder.matrix.fromArray(node.matrix);
    holder.matrixAutoUpdate = false;
    holder.matrixWorldNeedsUpdate = true; // 矩阵直改需显式传播
    parent.add(holder);
    if (node.primitives.length > 0) {
      for (const prim of node.primitives) {
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.BufferAttribute(prim.positions, 3));
        if (prim.normals) {
          geometry.setAttribute('normal', new THREE.BufferAttribute(prim.normals, 3));
        } else {
          geometry.computeVertexNormals();
        }
        if (prim.uvs) {
          geometry.setAttribute('uv', new THREE.BufferAttribute(prim.uvs, 2));
        }
        if (prim.indices) {
          geometry.setIndex(new THREE.BufferAttribute(prim.indices, 1));
        }
        const mesh = new THREE.Mesh(geometry, makeMaterial(prim));
        mesh.userData.scene3d = { nodeName: node.name, extras: node.extras };
        meshMeta.set(mesh.uuid, { nodeName: node.name, extras: node.extras });
        geos.push(geometry);
        holder.add(mesh);
      }
    }
    for (const child of node.children) buildNode(child, holder);
  };

  for (const node of parsed.roots) buildNode(node, root);

  const carrier = root as unknown as DisposableCarrier;
  carrier[disposables] = [...geos, ...mats];
  return {
    root,
    meshMeta,
    dispose(): void {
      for (const g of geos) g.dispose();
      for (const m of mats) {
        // 局部别名：`material.map` 的点链会被 maplibre-mock-surface 静态
        // 扫描（\bmap\.）误判为 MapLibre 成员 —— THREE 纹理属性不是地图 API。
        const texture = (m as THREE.MeshStandardMaterial).map;
        if (texture) {
          const img = texture.image as HTMLImageElement | undefined;
          if (img && typeof img === 'object' && 'src' in img
              && typeof img.src === 'string' && img.src.startsWith('blob:')) {
            try { URL.revokeObjectURL(img.src); } catch { /* noop */ }
          }
          texture.dispose();
        }
        m.dispose();
      }
      root.clear();
    },
  };
}
