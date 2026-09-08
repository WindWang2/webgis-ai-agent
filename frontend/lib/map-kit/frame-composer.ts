/**
 * W9（ADR-0118）：多帧导出运行时 —— atlas pages（PDF 多页）与
 * small-multiple grid（单画布网格拼板）。
 *
 * 逐帧确定性执行：保存受影响图层 filter/相机 → apply（where → setFilter
 * ["==",["get",field],equal]，`-label` 子层同步；extent → fitBounds）→
 * 有界 idle 等待（复用 exporter 的 30s 预算）→ 抓 canvas → 恢复原状。
 *
 * 诚实降级：上限 50 帧（超出截断 + atlas_page_limit_truncated）；单帧失败
 * 跳过 + atlas_page_skipped（detail=帧序号/标题）继续；frames 带
 * projection:'cartogram' → cartogram_unsupported（warning）并按未变形几何
 * 渲染（不伪造变形）。
 */
import type { Map } from 'maplibre-gl';
import type { ExportDegradation } from './export-chrome';

/** 帧筛选条件 → map.setFilter 表达式（["==", ["get", field], equal]）。 */
export interface ExportFrameWhere {
  layerId: string;
  field: string;
  equal: string | number;
}

export interface ExportFrame {
  title?: string;
  where?: ExportFrameWhere;
  /** [w, s, e, n]（与 inset bbox 同序）。 */
  extent?: [number, number, number, number];
  /**
   * 请求侧投影意图。仅接受 'cartogram' 一词并**显式不支持**：发
   * cartogram_unsupported 后按未变形几何渲染（诚实降级，不伪造变形）。
   */
  projection?: string;
}

export type FrameLayout = 'pages' | 'grid';

/** 单帧上限（与后端图集页上限同口径；超出确定性截断）。 */
export const MAX_FRAMES = 50;

/** 拼板画布单边安全上限（Safari 硬限 16384px；Chrome 面积限同量级）。 */
export const GRID_MAX_DIM_PX = 16384;

// grid 拼板常量（逻辑像素；帧 canvas 已含 dpi 增益）
export const GRID_MARGIN = 12;
export const GRID_GAP = 12;
export const GRID_CAPTION_H = 28;

export interface FrameComposerDeps {
  map: Map;
  /** 有界 idle 等待（exporter 的 waitForMapIdle —— 30s 预算）。 */
  waitForIdle: (map: Map, timeoutMs: number) => Promise<void>;
  idleTimeoutMs?: number;
}

export interface FrameComposeResult {
  canvases: HTMLCanvasElement[];
  titles: string[];
  degradations: ExportDegradation[];
}

interface SavedFilter {
  existed: boolean;
  filter?: unknown;
}

function frameTitle(frame: ExportFrame, index: number): string {
  if (frame.title) return frame.title;
  if (frame.where) return `${frame.where.field} = ${frame.where.equal}`;
  return `帧 ${index + 1}`;
}

/**
 * 逐帧执行抓帧：确定性顺序、失败跳过继续、结束时恢复受影响状态。
 * 返回的 canvases 供 pages（PDF addPage）/grid（单画布拼板）组合。
 */
export async function composeFrames(
  deps: FrameComposerDeps,
  frames: ExportFrame[],
): Promise<FrameComposeResult> {
  const { map, waitForIdle, idleTimeoutMs } = deps;
  const degradations: ExportDegradation[] = [];

  // 上限截断（先于逐帧执行 —— 不做无界工作）
  let workload = frames;
  if (frames.length > MAX_FRAMES) {
    workload = frames.slice(0, MAX_FRAMES);
    degradations.push({
      code: 'atlas_page_limit_truncated',
      detail: `${frames.length}→${MAX_FRAMES}`,
    });
  }

  // 相机一次性保存（extent 帧设置的是绝对 bounds，帧间不互相污染）
  const savedCamera = {
    center: map.getCenter(),
    zoom: map.getZoom(),
    bearing: map.getBearing(),
    pitch: map.getPitch(),
  };

  const canvases: HTMLCanvasElement[] = [];
  const titles: string[] = [];

  for (let i = 0; i < workload.length; i++) {
    const frame = workload[i];
    const title = frameTitle(frame, i);

    // cartogram 显式不支持 —— 按未变形几何渲染（诚实降级，不伪造变形）
    if (frame.projection === 'cartogram') {
      degradations.push({
        code: 'cartogram_unsupported',
        detail: `帧「${title}」：cartogram 投影不支持，按未变形几何渲染`,
      });
    }

    // where → 受影响图层（主层 + `-label` 子层，与 runtime 子层 id 约定一致）
    const touched: Array<{ id: string; saved: SavedFilter }> = [];
    try {
      if (frame.where) {
        const { layerId, field, equal } = frame.where;
        const ids = [layerId, `${layerId}-label`].filter(
          (id) => !!map.getLayer(id),
        );
        for (const id of ids) {
          let saved: SavedFilter = { existed: false };
          try {
            const f = map.getFilter(id);
            if (f !== undefined) saved = { existed: true, filter: f };
          } catch {
            /* 图层无 filter 可读 → 按不存在恢复 */
          }
          touched.push({ id, saved });
          map.setFilter(id, ['==', ['get', field], equal]);
        }
      }

      if (frame.extent) {
        const [w, s, e, n] = frame.extent;
        map.fitBounds(
          [
            [w, s],
            [e, n],
          ],
          { duration: 0, padding: 0 },
        );
      }

      await waitForIdle(map, idleTimeoutMs ?? 30_000);

      // 逐帧位图快照（review-r2 BLOCKER 修复）：MapLibre `getCanvas()` 每次返回
      // 同一个 live canvas 实例（`getCanvas(){return this.canvas}`），直接持引用
      // 会让所有帧都指向同一画布 —— 导出产物全部是最后一帧的内容（且不发射任何
      // 降级诊断，纯静默错帧）。必须在下一帧改变相机/filter 前 copy 出独立画布。
      const live = map.getCanvas();
      const snapshot = document.createElement('canvas');
      snapshot.width = live.width;
      snapshot.height = live.height;
      const sctx = snapshot.getContext('2d');
      if (!sctx) throw new Error('快照画布 2d 上下文不可用');
      sctx.drawImage(live, 0, 0);
      canvases.push(snapshot);
      titles.push(title);
    } catch (e) {
      // 单帧失败跳过 + 显式披露（帧序号/标题），继续后续帧
      degradations.push({
        code: 'atlas_page_skipped',
        detail: `${i + 1}/${title}${e instanceof Error ? `：${e.message}` : ''}`,
      });
    } finally {
      // filter 逐帧恢复（帧间不泄漏；初始无 filter → null 清除）
      for (const { id, saved } of touched) {
        map.setFilter(id, saved.existed ? (saved.filter as never) : (null as never));
      }
    }
  }

  // 相机恢复（原状 —— 导出后 live 视图不被多帧流程改变）
  map.jumpTo({
    center: savedCamera.center,
    zoom: savedCamera.zoom,
    bearing: savedCamera.bearing,
    pitch: savedCamera.pitch,
  });

  return { canvases, titles, degradations };
}

/**
 * grid 拼板：单画布行优先网格（cols = ceil(sqrt(n))），每帧下方小标题带。
 * 输出为位图画布（png 上传 / svg 位图回退 / pdf 单页嵌入的公共形态）。
 */
export function composeGridCanvas(
  canvases: HTMLCanvasElement[],
  titles: string[],
): HTMLCanvasElement {
  const n = canvases.length;
  const cols = Math.max(1, Math.ceil(Math.sqrt(n)));
  const rows = Math.ceil(n / cols);
  const cellW = Math.max(...canvases.map((c) => c.width));
  const cellH = Math.max(...canvases.map((c) => c.height));

  const width = GRID_MARGIN * 2 + cols * cellW + (cols - 1) * GRID_GAP;
  const height =
    GRID_MARGIN * 2 + rows * (cellH + GRID_CAPTION_H) + (rows - 1) * GRID_GAP;

  // review-r2（MAJOR）：拼板画布尺寸守卫 —— 高 dpi × 50 帧时 width/height 可
  // 超浏览器画布上限（Safari 16384px 硬限 / Chrome ~16384² 面积限），超限画布
  // 绘制静默失效，会**成功上传一张空白拼板图**（伪成功）。诚实失败：可读报错
  // 经 runExport catch 进入「导出失败」消息，用户减帧/降 dpi 后重试。
  if (width > GRID_MAX_DIM_PX || height > GRID_MAX_DIM_PX) {
    throw new Error(
      `多帧拼板画布 ${width}×${height}px 超出浏览器上限 ${GRID_MAX_DIM_PX}px（帧数 × dpi 过高）—— 请减少帧数或降低导出 dpi`,
    );
  }

  const grid = document.createElement('canvas');
  grid.width = width;
  grid.height = height;
  const ctx = grid.getContext('2d');
  if (!ctx) return grid;
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, width, height);

  for (let i = 0; i < n; i++) {
    const col = i % cols;
    const row = Math.floor(i / cols);
    const dx = GRID_MARGIN + col * (cellW + GRID_GAP);
    const dy = GRID_MARGIN + row * (cellH + GRID_CAPTION_H + GRID_GAP);
    const src = canvases[i];
    ctx.drawImage(src, dx, dy, src.width, src.height);
    // 每帧小标题带（截断 40 字符 —— 网格单元宽度的确定性口径）
    ctx.fillStyle = 'rgba(15,23,42,0.78)';
    ctx.fillRect(dx, dy + src.height, src.width, GRID_CAPTION_H);
    ctx.fillStyle = '#f8fafc';
    ctx.font = 'bold 15px sans-serif';
    ctx.textAlign = 'left';
    const title = titles[i] ?? `帧 ${i + 1}`;
    ctx.fillText(title.slice(0, 40), dx + 8, dy + src.height + 19);
  }
  return grid;
}
