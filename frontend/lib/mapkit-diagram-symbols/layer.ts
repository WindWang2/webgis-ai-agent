/**
 * layer.ts —— MapLibre GL JS CustomLayerInterface 工厂：把 oracle 图符几何
 * 以 per-vertex 颜色绘制到地图 canvas（glyph screen-space px，DPR-aware）。
 *
 * 渲染路径（简单稳健优先，使命约定的方案）：
 * 1. render() 时对每个 glyph 实例用 map.project(lngLat) 得到**CSS px** 中心；
 * 2. DPR = gl.drawingBufferWidth / canvas.clientWidth（backing store 与 CSS
 *    px 之比，避开 window.devicePixelRatio 假设；project 输出是 CSS px，
 *    drawingBuffer 是 CSS × devicePixelRatio）；
 * 3. vertices.ts 的 glyph-local 屏幕偏移 × DPR 转设备 px，与中心相加后
 *    用 canvas 尺寸换算 clip space（x: 2v/W-1，y: 1-2v/H 翻转）；
 * 4. CPU 构建交错顶点流（clipPos + rgba），单次 drawArrays(TRIANGLES)；
 *    引线为 2 顶点 LINES 图元，uniform 颜色。
 *
 * 状态纪律：blend 用 blendFuncSeparate(SRC_ALPHA, ONE_MINUS_SRC_ALPHA, ...)
 * （MapLibre 文档对非预乘 alpha 的建议）；不改 depth/stencil；三角化结果
 * 按 geometry 对象弱缓存（geometry 是 oracle 的稳定输出）。
 */

import type {
  CustomLayerInterface,
  CustomRenderMethodInput,
  Map as MaplibreMap,
} from "maplibre-gl";
import type { DiagramGlyph } from "./oracle";
import { triangulateGlyph, parseHexColor, type TriangulatedGlyph } from "./vertices";

export const DIAGRAM_SYMBOL_LAYER_DEFAULT_ID = "diagram-symbols";

/** 单个待渲染图符实例（oracle 几何 + 地理锚点 + palette）。 */
export interface DiagramGlyphInstance {
  /** 锚点经纬度 [lng, lat]（引线起点）。 */
  position: [number, number];
  /** oracle 的图符几何 dict（buildGlyphGeometry 输出）。 */
  geometry: DiagramGlyph;
  /** 类别序 hex 颜色表（逐顶点取模循环）。 */
  palette: string[];
  /** 真实锚点（未被避让位移前的经纬度）。 */
  anchor: [number, number];
  /** 布局证据：是否需要引线。 */
  leaderLine: boolean;
  /** 位移量 px（布局输出）。 */
  displacementPx: number;
  /** 包络半径 px @1x（布局输出）。 */
  radiusPx: number;
  /** 缩放层级门（布局输出）：当前 zoom < minZoom 的图符本帧跳过（小图符晚出现）。 */
  minZoom?: number;
}

export interface DiagramSymbolLayerOptions {
  /** MapLibre layer id（mapspec-runtime 侧习惯拼 LAYER_ID_SUFFIX）。 */
  id?: string;
  /** 初始图符实例（后续用 setGlyphs 更新）。 */
  glyphs?: DiagramGlyphInstance[];
  /** 引线颜色（hex，缺省深灰）。 */
  leaderLineColor?: string;
}

export interface DiagramSymbolCustomLayer extends CustomLayerInterface {
  /** 替换待渲染图符实例集合（下一帧生效）。 */
  setGlyphs(glyphs: DiagramGlyphInstance[]): void;
  getGlyphs(): DiagramGlyphInstance[];
}

const VERT_SRC = `
attribute vec2 aClipPos;
attribute vec4 aColor;
varying vec4 vColor;
void main() {
  gl_Position = vec4(aClipPos, 0.0, 1.0);
  vColor = aColor;
}
`;

const FRAG_SRC = `
precision mediump float;
varying vec4 vColor;
void main() {
  gl_FragColor = vColor;
}
`;

const FILL_STRIDE_FLOATS = 6; // clipX, clipY, r, g, b, a

export function createDiagramSymbolCustomLayer(
  options: DiagramSymbolLayerOptions = {},
): DiagramSymbolCustomLayer {
  const layerId = options.id ?? DIAGRAM_SYMBOL_LAYER_DEFAULT_ID;
  const leaderColor = parseHexColor(options.leaderLineColor ?? "#334155");

  let glyphs: DiagramGlyphInstance[] = options.glyphs ? [...options.glyphs] : [];
  let mapRef: MaplibreMap | null = null;
  let glRef: WebGL2RenderingContext | null = null;
  let program: WebGLProgram | null = null;
  let fillBuffer: WebGLBuffer | null = null;
  let leaderBuffer: WebGLBuffer | null = null;
  let aClipPos = -1;
  let aColor = -1;

  function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader | null {
    const shader = gl.createShader(type);
    if (shader === null) return null;
    gl.shaderSource(shader, src);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      gl.deleteShader(shader);
      return null;
    }
    return shader;
  }

  const layer: DiagramSymbolCustomLayer = {
    id: layerId,
    type: "custom",
    renderingMode: "2d",

    onAdd(map: MaplibreMap, gl: WebGL2RenderingContext): void {
      mapRef = map;
      glRef = gl;
      const vs = compile(gl, gl.VERTEX_SHADER, VERT_SRC);
      const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG_SRC);
      if (vs !== null && fs !== null) {
        program = gl.createProgram();
        if (program !== null) {
          gl.attachShader(program, vs);
          gl.attachShader(program, fs);
          gl.linkProgram(program);
          if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
            gl.deleteProgram(program);
            program = null;
          }
        }
      }
      gl.deleteShader(vs);
      gl.deleteShader(fs);
      fillBuffer = gl.createBuffer();
      leaderBuffer = gl.createBuffer();
      if (program !== null) {
        aClipPos = gl.getAttribLocation(program, "aClipPos");
        aColor = gl.getAttribLocation(program, "aColor");
      }
    },

    render(gl: WebGL2RenderingContext, _renderOptions: CustomRenderMethodInput): void {
      if (mapRef === null || glyphs.length === 0) return;
      // 缩放层级门（布局 minZoom；NaN/缺省=不过滤）。
      const currentZoom = mapRef.getZoom();
      const visibleGlyphs = glyphs.filter(
        (g) => !(typeof g.minZoom === "number" && Number.isFinite(g.minZoom)
                 && currentZoom < g.minZoom),
      );
      if (visibleGlyphs.length === 0) return;
      const canvas = mapRef.getCanvas();
      const cssWidth = canvas.clientWidth > 0 ? canvas.clientWidth : gl.drawingBufferWidth;
      const cssHeight = canvas.clientHeight > 0 ? canvas.clientHeight : gl.drawingBufferHeight;
      if (cssWidth <= 0 || cssHeight <= 0 || gl.drawingBufferWidth <= 0) return;
      // DPR = backing store px / CSS px（project 输出为 CSS px）。
      const dprX = gl.drawingBufferWidth / cssWidth;
      const dprY = gl.drawingBufferHeight / cssHeight;
      const bufferW = gl.drawingBufferWidth;
      const bufferH = gl.drawingBufferHeight;

      // 1) 每实例三角化（palette 按实例）+ 中心投影，统计顶点总量。
      interface PreparedGlyph {
        center: [number, number]; // 设备 px
        triangulated: TriangulatedGlyph;
      }
      const prepared: PreparedGlyph[] = [];
      let fillVertexCount = 0;
      let leaderVertexCount = 0;
      for (const instance of visibleGlyphs) {
        const point = mapRef.project([instance.position[0], instance.position[1]]);
        const center: [number, number] = [point.x * dprX, point.y * dprY];
        const tri = triangulateGlyph(instance.geometry, instance.palette);
        prepared.push({ center, triangulated: tri });
        fillVertexCount += tri.vertexCount;
        if (instance.leaderLine && instance.displacementPx > 0) leaderVertexCount += 2;
      }
      if (fillVertexCount === 0 && leaderVertexCount === 0) return;

      // 2) CPU 构建交错顶点流（clip space）。
      const fill = new Float32Array(fillVertexCount * FILL_STRIDE_FLOATS);
      let o = 0;
      for (const item of prepared) {
        const [cx, cy] = item.center;
        const verts = item.triangulated.vertices;
        for (let i = 0; i < item.triangulated.vertexCount; i++) {
          const base = i * FILL_STRIDE_FLOATS;
          const px = cx + verts[base] * dprX;
          const py = cy + verts[base + 1] * dprY;
          fill[o] = (px / bufferW) * 2 - 1;
          fill[o + 1] = 1 - (py / bufferH) * 2;
          fill[o + 2] = verts[base + 2];
          fill[o + 3] = verts[base + 3];
          fill[o + 4] = verts[base + 4];
          fill[o + 5] = verts[base + 5];
          o += FILL_STRIDE_FLOATS;
        }
      }

      // 3) 引线（anchor → position，2 顶点 LINES）。
      let leader: Float32Array | null = null;
      if (leaderVertexCount > 0) {
        leader = new Float32Array(leaderVertexCount * 2);
        let lo = 0;
        for (const instance of visibleGlyphs) {
          if (!(instance.leaderLine && instance.displacementPx > 0)) continue;
          const from = mapRef.project([instance.anchor[0], instance.anchor[1]]);
          const to = mapRef.project([instance.position[0], instance.position[1]]);
          leader[lo] = (from.x * dprX / bufferW) * 2 - 1;
          leader[lo + 1] = 1 - (from.y * dprY / bufferH) * 2;
          leader[lo + 2] = (to.x * dprX / bufferW) * 2 - 1;
          leader[lo + 3] = 1 - (to.y * dprY / bufferH) * 2;
          lo += 4;
        }
      }

      // 4) 绘制。
      if (program === null) return;
      gl.useProgram(program);
      gl.enable(gl.BLEND);
      gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      if (fillVertexCount > 0 && fillBuffer !== null) {
        gl.bindBuffer(gl.ARRAY_BUFFER, fillBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, fill, gl.DYNAMIC_DRAW);
        gl.enableVertexAttribArray(aClipPos);
        gl.vertexAttribPointer(aClipPos, 2, gl.FLOAT, false, FILL_STRIDE_FLOATS * 4, 0);
        gl.enableVertexAttribArray(aColor);
        gl.vertexAttribPointer(aColor, 4, gl.FLOAT, false, FILL_STRIDE_FLOATS * 4, 2 * 4);
        gl.drawArrays(gl.TRIANGLES, 0, fillVertexCount);
      }
      if (leader !== null && leaderVertexCount > 0 && leaderBuffer !== null) {
        gl.bindBuffer(gl.ARRAY_BUFFER, leaderBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, leader, gl.DYNAMIC_DRAW);
        gl.disableVertexAttribArray(aColor);
        gl.vertexAttrib4f(aColor, leaderColor.r, leaderColor.g, leaderColor.b, leaderColor.a);
        gl.enableVertexAttribArray(aClipPos);
        gl.vertexAttribPointer(aClipPos, 2, gl.FLOAT, false, 2 * 4, 0);
        gl.drawArrays(gl.LINES, 0, leaderVertexCount);
      }
    },

    onRemove(): void {
      const gl = glRef;
      if (gl !== null) {
        if (fillBuffer !== null) gl.deleteBuffer(fillBuffer);
        if (leaderBuffer !== null) gl.deleteBuffer(leaderBuffer);
        if (program !== null) gl.deleteProgram(program);
      }
      fillBuffer = null;
      leaderBuffer = null;
      program = null;
      glRef = null;
      mapRef = null;
    },

    setGlyphs(next: DiagramGlyphInstance[]): void {
      glyphs = [...next];
    },

    getGlyphs(): DiagramGlyphInstance[] {
      return [...glyphs];
    },
  };

  return layer;
}
