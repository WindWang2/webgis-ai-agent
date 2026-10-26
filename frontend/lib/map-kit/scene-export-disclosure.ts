/**
 * G3D05（ADR-0275）：三维场景导出诚实披露 —— 纯函数单源。
 *
 * 语义与后端 scene_quality.py 同口径（不复制实现，语义常量镜像并在
 * 契约测试中锁定一致性）：
 * - 有效 pitch = view.pitch（当前相机）优先，回退 scene.camera.pitch；
 * - pitch > SCALE_TRUST_PITCH_MAX（10°）时屏幕比例尺被透视压缩失真：
 *   有 scene_disclosure 组件在场 → 披露已挂（info 语义）；缺披露且仍
 *   画比例尺 → 发 `scale_unreliable_perspective` warning（静默误导
 *   不可接受）；
 * - 矢量导出（svg/pdf 矢量孪生）无法保留透视模型 → `svg_3d_perspective_degrade`
 *   收据（后端孪生编译期发射；本模块提供同语义判定供前端矢量路径复用）。
 */

/** 屏幕比例尺可信阈（°）—— 后端 scene_quality._SCALE_TRUST_PITCH_MAX 镜像。 */
export const SCALE_TRUST_PITCH_MAX = 10;

export type ScaleTrust = 'reliable' | 'perspective' | 'unknown';

export interface SceneExportDisclosureInput {
  /** 当前相机 pitch（°）；导出时刻的真相。 */
  viewPitch?: number | null;
  /** spec.scene.camera.pitch（推荐档）；view 缺席时回退。 */
  scenePitch?: number | null;
  /** 导出件是否绘制比例尺（enabled 的 scale_bar 组件或 chrome 注入兜底）。 */
  hasScaleBar: boolean;
  /** 是否有 enabled 的 scene_disclosure 组件随图。 */
  hasSceneDisclosure: boolean;
}

export interface SceneExportDisclosure {
  scaleTrust: ScaleTrust;
  /** 透视下仍画比例尺 → 必须发 warning 诊断（披露组件在场时降为 info 语义，仍建议发码）。 */
  scaleDiagnostic: { code: 'scale_unreliable_perspective'; detail: string } | null;
  /** 比例尺旁的披露文案（画布/矢量两侧同文）。 */
  scaleNoteZh: string | null;
}

export function effectivePitch(
  viewPitch?: number | null,
  scenePitch?: number | null,
): number | null {
  if (typeof viewPitch === 'number' && Number.isFinite(viewPitch)) {
    return viewPitch;
  }
  if (typeof scenePitch === 'number' && Number.isFinite(scenePitch)) {
    return scenePitch;
  }
  return null;
}

export function sceneExportDisclosure(
  input: SceneExportDisclosureInput,
): SceneExportDisclosure {
  const pitch = effectivePitch(input.viewPitch, input.scenePitch);
  if (pitch === null) {
    return { scaleTrust: 'unknown', scaleDiagnostic: null, scaleNoteZh: null };
  }
  if (pitch <= SCALE_TRUST_PITCH_MAX) {
    return { scaleTrust: 'reliable', scaleDiagnostic: null, scaleNoteZh: null };
  }
  if (!input.hasScaleBar) {
    // 无比例尺即无误导面 —— 不发码（与后端 quality 门同口径）。
    return { scaleTrust: 'perspective', scaleDiagnostic: null, scaleNoteZh: null };
  }
  const note = input.hasSceneDisclosure
    ? `透视视角（pitch ${Math.round(pitch)}°）：比例尺仅供参考`
    : `透视视角（pitch ${Math.round(pitch)}°）：比例尺不可信，仅供参考`;
  return {
    scaleTrust: 'perspective',
    scaleDiagnostic: {
      code: 'scale_unreliable_perspective',
      detail: String(SCALE_TRUST_PITCH_MAX),
    },
    scaleNoteZh: note,
  };
}

export interface VectorDegradeReceipt {
  code: 'svg_3d_perspective_degrade';
  detail: string;
}

/**
 * 矢量导出的场景降级收据：scene.mode ∈ {3d, 2.5d} 时必发（2d 语义
 * 零噪音）。后端孪生（mapspec_to_svg）与本模块同判定 —— 契约测试
 * 锁两端一致。
 */
export function vectorSceneDegradeReceipt(
  sceneMode?: string | null,
): VectorDegradeReceipt | null {
  if (sceneMode === '3d' || sceneMode === '2.5d') {
    return { code: 'svg_3d_perspective_degrade', detail: sceneMode };
  }
  return null;
}
