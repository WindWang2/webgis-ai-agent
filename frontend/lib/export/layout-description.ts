/**
 * ac-08（ADR-0157 P2）· 出版版面描述中间层（Publication Layout IR）。
 *
 * 目标：canvas 与 SVG 两条渲染链从**同一份版面描述**出图，消除双链漂移
 *（recon §3 差异表：图例只取 [0]、署名/水印缺席、比例尺口径分叉、标题
 * 回退链不一致等）。
 *
 * 分工（与 07 线的协调契约：07 未合入 → 本线定义，PR 置顶贴出；07 合入后
 * 可整体迁往其「版面描述」模块，本文件即交接面）：
 * - buildPublicationLayout：纯同步装配器 —— 输入既有单源决策件
 *   （buildExportChrome 的 chrome 模型、scale-math 比例尺、extent.ts 范围），
 *   产出确定性、JSON 可序列化的版面描述。**不**引入新决策，只把分散在
 *   消费端的决策收拢为一处记录。
 * - 消费方：
 *   · canvas 链（composeLayout）：消费 chromeModel 字段（行为不变，既有
 *     2657 行整饰绘制器与金样测试保持 pin）；
 *   · SVG 链（vector-svg-export）：消费全部字段 —— 图例多实例、署名、
 *     比例尺数字、标题回退链、范围披露全部与 canvas 同记录；
 *   · 后端（P6）：`app/lib/cartography/layout_description.py` 忠实镜像
 *     本模块，golden fixtures 双语言逐字段对拍。
 * - PDF 链（P3）：texts 字段 = PDF 真实文本层的单一事实源（含 CJK 字体
 *   选择标志），画布 skipTitle 决策与 PDF doc.text 内容同源。
 */
import type { ExportChromeModel, ExportDegradation } from '../map-kit/export-chrome';
import {
  computeNiceScale,
  formatScaleLabel,
  type NiceScale,
} from '../map-kit/scale-math';

export type ColorMode = 'srgb' | 'cmyk';

/** 页面档位。bleedMm 仅出版档（cmyk）> 0，cropMarks 同步开启（§2 P5）。 */
export interface PublicationPage {
  paperSize: 'screen' | 'A4' | 'A3';
  orientation: 'landscape' | 'portrait';
  dpi: number;
  /** 地图图框设备像素尺寸（含出血前）。 */
  widthPx: number;
  heightPx: number;
  colorMode: ColorMode;
  /** 出血（毫米）；srgb 档恒 0。 */
  bleedMm: number;
  /** 裁切标记；仅出版档 true。 */
  cropMarks: boolean;
}

/** PDF/SVG 真实文本层条目（P3：单一事实源）。 */
export interface PublicationText {
  kind: 'title' | 'subtitle' | 'attribution' | 'dataSource' | 'author';
  /** 回退链决策后的最终文本（空串 = 不进文本层）。 */
  text: string;
}

/** 范围契约（P4 所见即所得）。 */
export interface PublicationExtent {
  /** live 视口遮罩范围（用户所见）。 */
  mask: [number, number, number, number] | null;
  /** 纸张图框导出范围（⊇ mask；screen 档 == mask）。 */
  export: [number, number, number, number] | null;
  /** 数据范围超出导出范围 → true（调用方发 extent_overflow_data 提示）。 */
  dataOverflow: boolean;
}

/** 出版版面描述（JSON 可序列化；后端镜像逐字段对拍）。 */
export interface PublicationLayout {
  version: 1;
  page: PublicationPage;
  /** 地图图框在导出画布内的像素矩形（画布链消费；SVG 链全幅即图框）。 */
  mapFrame: { x: number; y: number; width: number; height: number };
  /** 既有 chrome 模型（canvas 链消费面；buildExportChrome 单源产出）。 */
  chromeModel: ExportChromeModel | null;
  texts: PublicationText[];
  /** 比例尺（scale-math 单源数字；null = 无米/像素依据，不画不虚构）。 */
  scaleBar: { metersPerPixel: number; nice: NiceScale; label: string } | null;
  extent: PublicationExtent;
  /** 装配期降级记录（chrome 模型降级 + 范围披露；消费方不得自行增删）。 */
  degradations: ExportDegradation[];
}

export interface BuildPublicationLayoutInput {
  paperSize: 'screen' | 'A4' | 'A3';
  orientation: 'landscape' | 'portrait';
  dpi: number;
  /** 导出画布（图框区）设备像素。 */
  frame: { width: number; height: number };
  chromeModel: ExportChromeModel | null;
  /** 标题回退链：请求参数 > spec 组件 > 空串（与 composeLayout 同序）。 */
  requestTitle?: string;
  specTitle?: string;
  requestSubtitle?: string;
  specSubtitle?: string;
  author?: string;
  dataSource?: string;
  attributionText?: string;
  metersPerPixel?: number;
  extent?: PublicationExtent;
  colorMode?: ColorMode;
  /** 比例尺条目标称像素长（与画布 drawChromeScaleBar 口径一致）。 */
  scaleBarTargetPx?: number;
}

export const PUBLICATION_LAYOUT_VERSION = 1 as const;

/** 出版档（cmyk）出血宽度（毫米）。 */
export const PUBLICATION_BLEED_MM = 3;

/**
 * 装配出版版面描述。纯函数、确定性：同输入同输出（后端镜像对拍前提）。
 */
export function buildPublicationLayout(
  input: BuildPublicationLayoutInput,
): PublicationLayout {
  const colorMode: ColorMode = input.colorMode ?? 'srgb';
  const bleedMm = colorMode === 'cmyk' ? PUBLICATION_BLEED_MM : 0;
  const page: PublicationPage = {
    paperSize: input.paperSize,
    orientation: input.orientation,
    dpi: input.dpi,
    widthPx: Math.round(input.frame.width),
    heightPx: Math.round(input.frame.height),
    colorMode,
    bleedMm,
    cropMarks: colorMode === 'cmyk',
  };

  // 标题回退链 —— 单处记录，canvas/SVG/PDF 三链共用（此前 SVG 链自定
  // `input.title || ''`，与画布 chrome 的请求>spec 回退链漂移）。
  const title = input.requestTitle || input.specTitle || '';
  const subtitle = input.requestSubtitle || input.specSubtitle || '';
  const texts: PublicationText[] = [];
  if (title) texts.push({ kind: 'title', text: title });
  if (subtitle) texts.push({ kind: 'subtitle', text: subtitle });
  if (input.author) texts.push({ kind: 'author', text: input.author });
  if (input.dataSource) texts.push({ kind: 'dataSource', text: input.dataSource });
  const attribution = input.attributionText || '';
  if (attribution) texts.push({ kind: 'attribution', text: attribution });

  // 比例尺 —— scale-math 单源数字（画布与 SVG 共用同一 nice 值，消除
  // 「SVG 条长固定 120px、画布 nice 归整」的口径分叉）。
  let scaleBar: PublicationLayout['scaleBar'] = null;
  const mpp = input.metersPerPixel;
  if (mpp && mpp > 0) {
    const nice = computeNiceScale(mpp, input.scaleBarTargetPx ?? 120);
    scaleBar = { metersPerPixel: mpp, nice, label: formatScaleLabel(nice.meters) };
  }

  const degradations: ExportDegradation[] = [...(input.chromeModel?.degradations ?? [])];
  if (input.extent?.dataOverflow) {
    degradations.push({
      code: 'extent_overflow_data',
      detail: '数据范围超出出图范围，超界部分以背景呈现（未静默裁切）',
    });
  }

  return {
    version: PUBLICATION_LAYOUT_VERSION,
    page,
    mapFrame: { x: 0, y: 0, width: page.widthPx, height: page.heightPx },
    chromeModel: input.chromeModel,
    texts,
    scaleBar,
    extent: input.extent ?? { mask: null, export: null, dataOverflow: false },
    degradations,
  };
}

/** 纸张图框纵横比（width/height；与 prepareExportCanvas 1.414 口径一致）。 */
export function frameAspectWH(
  paperSize: 'screen' | 'A4' | 'A3',
  orientation: 'landscape' | 'portrait',
): number {
  const ratio = 1.414;
  return orientation === 'portrait' ? 1 / ratio : ratio;
}
