/**
 * scene3d/custom-layer — MapLibre CustomLayerInterface × THREE 桥（G3D02 M3）。
 *
 * 桥语义（工作单 D1 决策：three + 自维护最小胶水）：
 * - 每资产一个 custom layer（id `scene3d-<assetId>`，renderingMode '3d'），
 *  共享 map 级单例 THREE.WebGLRenderer（MapLibre 的 gl context 复用）。
 * - render(gl, args)：把 `args.modelViewProjectionMatrix`（mercator→clip）
 *  灌入正交相机投影面 —— three 在世界（mercator）坐标绘制；深度测试开启
 *  （与 MapLibre 已绘内容的遮挡关系由 depth buffer 决定；terrain 在场时
 *  是近似遮挡 —— 能力边界已在 DECISIONS.md D4 声明）。
 * - onAdd/onRemove 生命周期由 manager 驱动；context lost = 整图重建路径
 *  （map-panel mapKey bump），本层不自行恢复。
 * - 事件驱动渲染：仅在内容变化时 triggerRepaint；无自建 rAF 循环。
 */

import * as THREE from 'three';

export interface Scene3dLayerHooks {
  /**
   * 帧回调（manager 注入：拾取矩阵快照 / 调度 tick）。
   *
   * @param mvp mercator 像素世界 → clip（MapLibre v6 语义：世界 =
   *   mercator[0,1] × worldSize，z = 海拔 × pixelPerMeter 同域 —— 见
   *   maplibre transform._viewProjMatrix 源：x/y 平移 merc[0,1]、z 缩放
   *   pixelPerMeter，透视距为像素单位）。
   * @param worldScale 本帧 worldSize（512·2^zoom；placement 乘它进像素域）。
   */
  onFrame?: (mvp: number[] | Float32Array, worldScale: number) => void;
}

export interface RendererCarrier {
  /** 共享 renderer（同 map 同 gl context 单例）。 */
  renderer: THREE.WebGLRenderer;
  camera: THREE.PerspectiveCamera;
  scene: THREE.Scene;
  /** renderer 心跳（多 layer 引用计数下最后一层移除时才 dispose）。 */
  refCount: number;
}

/** map → 共享 renderer 载体（WeakMap：map 重建即随 GC 释放）。 */
const carriers = new WeakMap<object, RendererCarrier>();

export function acquireRenderer(map: object, gl?: WebGLRenderingContext | WebGL2RenderingContext): RendererCarrier | null {
  let carrier = carriers.get(map);
  if (carrier) {
    carrier.refCount += 1;
    return carrier;
  }
  if (!gl) return null;
  try {
    const renderer = new THREE.WebGLRenderer({
      canvas: (gl as WebGL2RenderingContext).canvas as HTMLCanvasElement, // 复用 MapLibre canvas
      context: gl as WebGL2RenderingContext,
      antialias: true,
      alpha: true,
    });
    renderer.autoClear = false;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    const camera = new THREE.PerspectiveCamera();
    camera.matrixAutoUpdate = false;
    // 光照：环境 + 半球（PBR 近似；无影 —— 能力边界）
    const scene = new THREE.Scene();
    scene.add(new THREE.AmbientLight(0xffffff, 1.6));
    const hemi = new THREE.HemisphereLight(0xffffff, 0x8d8d8d, 1.1);
    hemi.position.set(0.2, -1, 0.3); // mercator 空间的"天"方向（z 上）
    scene.add(hemi);
    carrier = { renderer, camera, scene, refCount: 1 };
    carriers.set(map, carrier);
    return carrier;
  } catch {
    return null;
  }
}

export function releaseRenderer(map: object): void {
  const carrier = carriers.get(map);
  if (!carrier) return;
  carrier.refCount -= 1;
  if (carrier.refCount <= 0) {
    carrier.scene.clear();
    carrier.renderer.dispose();
    carriers.delete(map);
  }
}

export function getCarrier(map: object): RendererCarrier | undefined {
  return carriers.get(map);
}

/**
 * 造一个资产的 custom layer。`group` 挂进共享 scene 的时机由 manager 控制
 * （visible 切换通过 group.visible，不增删 layer）。
 */
export function makeScene3dCustomLayer(params: {
  assetId: string;
  map: object;
  group: THREE.Group;
  /** [0,1] mercator 域放置矩阵（manager 持有；每帧 × worldScale）。 */
  placement: () => number[];
  hooks?: Scene3dLayerHooks;
  /** 渲染降档（弱机：关抗锯齿/光照简化由 manager 在 renderer 上配置）。 */
  onRenderError?: (err: unknown) => void;
}): {
  id: string;
  type: 'custom';
  renderingMode: '3d';
  onAdd: (map: unknown, gl: WebGLRenderingContext | WebGL2RenderingContext) => void;
  onRemove: (map: unknown, gl: WebGLRenderingContext | WebGL2RenderingContext) => void;
  render: (gl: WebGLRenderingContext | WebGL2RenderingContext, args: unknown) => void;
} {
  const layerId = `scene3d-${params.assetId}`;
  let carrier: RendererCarrier | null = null;
  let groupMounted = false;
  const scaleMat = new THREE.Matrix4();

  const mapRef = params.map as { getZoom?: () => number } | undefined;
  const layer = {
    id: layerId,
    type: 'custom' as const,
    renderingMode: '3d' as const,
    map: mapRef,
    onAdd(_map: unknown, gl: WebGLRenderingContext | WebGL2RenderingContext) {
      carrier = acquireRenderer(params.map, gl);
      if (carrier && !groupMounted) {
        carrier.scene.add(params.group);
        groupMounted = true;
      }
    },
    /** 本帧 worldSize（MapLibre transform tileSize=512 × 2^zoom）。 */
    _worldScale(this: { map?: { getZoom?: () => number } }): number {
      const zoom = this.map?.getZoom?.();
      return 512 * Math.pow(2, typeof zoom === 'number' && Number.isFinite(zoom) ? zoom : 0);
    },
    onRemove(_map: unknown, _gl: unknown) {
      if (carrier && groupMounted) {
        carrier.scene.remove(params.group);
        groupMounted = false;
      }
      releaseRenderer(params.map);
      carrier = null;
    },
    render(gl: WebGLRenderingContext | WebGL2RenderingContext, args: unknown) {
      if (!carrier) {
        carrier = acquireRenderer(params.map, gl);
        if (!carrier) return;
        if (!groupMounted) {
          carrier.scene.add(params.group);
          groupMounted = true;
        }
      }
      const mvp = extractMvp(args);
      if (!mvp) return;
      // 本帧世界尺度：placement（[0,1] mercator 域）× W 进像素世界。
      const layerThis = this as unknown as { map?: { getZoom?: () => number }; _worldScale(): number };
      const worldScale = layerThis._worldScale();
      applyPlacement(params.group, params.placement(), worldScale, scaleMat);
      try {
        carrier.camera.projectionMatrix.fromArray(mvp);
        carrier.camera.projectionMatrixInverse.copy(carrier.camera.projectionMatrix).invert();
        params.hooks?.onFrame?.(mvp, worldScale);
        const renderer = carrier.renderer;
        renderer.resetState();
        renderer.render(carrier.scene, carrier.camera);
      } catch (err) {
        params.onRenderError?.(err);
      }
    },
  };
  return layer;
}

/**
 * 应用像素世界放置：group.matrix = S(worldScale) · placement。
 * （均匀缩放与旋转交换 → 等价 T(anchor·W)·R·F·S(meterUnit·W)；
 * MapLibre mercator-z 与 x/y 同 ×W —— 见 hooks.onFrame 语义注记。）
 */
export function applyPlacement(
  group: THREE.Group,
  placement: number[],
  worldScale: number,
  scratch: THREE.Matrix4,
): void {
  scratch.makeScale(worldScale, worldScale, worldScale);
  group.matrix.fromArray(placement).premultiply(scratch);
  group.matrixAutoUpdate = false;
  group.matrixWorldNeedsUpdate = true;
}

interface RenderArgsLike {
  modelViewProjectionMatrix?: number[];
  defaultProjectionData?: { mainMatrix?: number[] };
}

function extractMvp(args: unknown): number[] | Float32Array | null {
  const a = args as RenderArgsLike | undefined;
  const mvp = a?.modelViewProjectionMatrix ?? a?.defaultProjectionData?.mainMatrix;
  // MapLibre 的 mat4 是 Float32Array（非普通数组 —— Array.isArray 会误拒）。
  if (mvp && typeof mvp === 'object' && mvp.length === 16) return mvp;
  return null;
}
