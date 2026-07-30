import type { CommandEntry } from './types';
import { API_BASE } from '@/lib/api/config';
import * as exporter from '@/lib/map-kit/exporter';
import { devOnly } from '@/lib/utils/logger';

/**
 * export_map command — the large async arm.
 *
 * This is candidate 2's future home, kept as its own file per the spec. The
 * `run` body is the verbatim extraction of the `export_map` `case` from
 * map-action-handler.tsx. It marks the pop as deferred (the component's finally
 * skips the synchronous pop) and pops itself via `ctx.safePop()` inside the
 * `map.once('render')` callback's finally. `useHudStore.getState()` becomes
 * `ctx.getHudState()`.
 */
export const exportCommands: Record<string, CommandEntry> = {
  export_map: {
    requiredParams: () => true,
    run(ctx) {
      const { map, params, getHudState, setDeferredPop, safePop } = ctx;
      const {
        title,
        subtitle,
        author = '',
        dataSource = '',
        showWatermark = true,
        showLegend = params?.showLegend ?? params?.include_legend ?? true,
        showCompass = params?.showCompass ?? params?.include_compass ?? true,
        showScale = params?.showScale ?? params?.include_scale ?? true,
        showMetadata = true,
        showGraticules = false,
        format = "png",
        paperSize = "screen",
        orientation = "landscape",
        dpi = 96
      } = params || {};

      // /review C11: surface a loading message — export upload can take seconds
      // (SVG/PDF over slow network) and currently the user sees nothing happen.
      try {
        getHudState().setPendingSystemMessage(
          `[系统通知] 正在生成 ${String(format).toUpperCase()} 导出文件…`
        );
      } catch {
        /* defensive */
      }

      const theme = getHudState().theme;
      // F5: 异步 export 必须等 map.once('render') 真正回调完再 popAction，
      // 否则连续触发 export 会让后一次在前一次还没合成完时覆盖 canvas。
      // 标记该 case 自己负责 popAction，外层 finally 跳过。
      setDeferredPop(true);

      map.once("render", async () => {
        // High-DPI: set MapLibre pixel ratio for true resolution capture
        const origPixelRatio = map.getPixelRatio();
        const targetPixelRatio = dpi / 96;
        try {
          if (targetPixelRatio > 1) {
            map.setPixelRatio(targetPixelRatio);
            // Wait for MapLibre to re-render at new resolution
            await new Promise<void>(resolve => map.once("idle", () => resolve()));
          }

          const baseCanvas = map.getCanvas();
          // Canvas is now at target DPI, so pass dpi=96 to avoid double-scaling
          const { canvas: exportCanvas, srcW } = exporter.prepareExportCanvas(baseCanvas, {
            paperSize: paperSize as any,
            orientation: orientation as any,
            dpi: 96
          });

          const storeState = getHudState();

          // Find legend_spec from any visible layer that has one
          const legendLayer = storeState.layers.find(
            (l: any) => l.visible && l.legend_spec
          );
          const legendSpec = legendLayer?.legend_spec;

          // Find thematic layer (choropleth/lisa) for legacy path
          const thematicLayerInfo = storeState.layers.find(
            (l: any) => l.visible && ((l.style as any)?.type === "choropleth" || (l.style as any)?.type === "lisa" || (l.source as any)?.metadata?.thematic_type === "choropleth")
          );
          const thematicLayer = (thematicLayerInfo?.style as any)?.type ? thematicLayerInfo?.style : thematicLayerInfo;

          // Find heatmap layer for gradient legend
          const heatmapLayer = storeState.layers.find(
            (l: any) => l.visible && l.type === 'heatmap'
          );
          const heatmapLegend = heatmapLayer ? { name: heatmapLayer.name } : undefined;

          exporter.composeLayout(exportCanvas, title || '', subtitle || '', {
            dpi,
            theme,
            showScale,
            showCompass,
            showWatermark,
            showLegend,
            showMetadata,
            showGraticules,
            author,
            dataSource,
            mapCenter: map.getCenter(),
            mapZoom: map.getZoom(),
            mapBearing: map.getBearing(),
            thematicLayer,
            legendSpec,
            heatmapLegend,
          });

          const dataUrl = exportCanvas.toDataURL("image/png");
          const res = await fetch(dataUrl);
          const blob = await res.blob();

          const fmt = (format ?? "png").toLowerCase();

          if (fmt === "svg") {
            // Wrap the rendered PNG inside an SVG container.
            // Downstream vector tools (Illustrator/Inkscape) can open this and
            // layer additional vector annotations on top of the raster basemap.
            const w = exportCanvas.width;
            const h = exportCanvas.height;
            const svg = `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><title>${(title || "map").replace(/[<>&]/g, "")}</title><image width="${w}" height="${h}" xlink:href="${dataUrl}"/></svg>`;
            const svgBlob = new Blob([svg], { type: "image/svg+xml" });
            const svgForm = new FormData();
            svgForm.append("file", svgBlob, "export.svg");
            if (title) svgForm.append("title", title);
            const svgRes = await fetch(`${API_BASE}/api/v1/export`, {
              method: "POST",
              body: svgForm,
            });
            if (!svgRes.ok) throw new Error("SVG export upload failed");
            const svgData = await svgRes.json();
            const svgUrl: string = svgData.url;
            getHudState().addExport({
              id: `export-${Date.now()}`,
              name: title || '未命名',
              filename: svgData.filename,
              type: 'svg',
              size: `${(svgBlob.size / 1024).toFixed(0)}KB`,
              date: new Date().toLocaleString(),
            });
            getHudState().setPendingSystemMessage(
              `[系统通知] 专题地图 SVG \`${title || "未命名"}\` 已成功生成 (含嵌入位图)，` +
                `文件已落盘并分配URL：${svgUrl}。可通过以下链接下载：[下载SVG](${API_BASE}${svgUrl})。注意展示完链接后直接结束。`
            );
          } else if (fmt === "pdf") {
            // Client-side PDF generation with jsPDF (vector text/lines)
            const pdfBlob = await exporter.exportToPDF(exportCanvas, title || '', subtitle, {
              paperSize: (paperSize === 'A3' ? 'A3' : 'A4') as 'A4' | 'A3',
              orientation: orientation as 'landscape' | 'portrait',
              author,
              dataSource,
            });

            const pdfForm = new FormData();
            pdfForm.append("file", pdfBlob, "export.pdf");
            if (title) pdfForm.append("title", title);

            const pdfRes = await fetch(`${API_BASE}/api/v1/export`, {
              method: "POST",
              body: pdfForm,
            });
            if (!pdfRes.ok) throw new Error(`PDF upload failed: ${pdfRes.status}`);
            const pdfData = await pdfRes.json();
            const pdfUrl: string = pdfData.url;
            getHudState().addExport({
              id: `export-${Date.now()}`,
              name: title || '未命名',
              filename: pdfData.filename,
              type: 'pdf',
              size: `${(pdfBlob.size / 1024).toFixed(0)}KB`,
              date: new Date().toLocaleString(),
            });
            getHudState().setPendingSystemMessage(
              `[系统通知] 专题底图 PDF \`${title || "未命名"}\` 已成功生成 (jsPDF 向量版)，` +
                `文件已落盘并分配URL：${pdfUrl}。` +
                `请告知用户 PDF 已就绪，可通过以下链接下载：[下载PDF](${API_BASE}${pdfUrl})。注意展示完链接后直接结束。`
            );
          } else {
            const formData = new FormData();
            formData.append("file", blob, "export.png");
            if (title) formData.append("title", title);

            const uploadRes = await fetch(`${API_BASE}/api/v1/export`, {
              method: "POST",
              body: formData,
            });
            if (!uploadRes.ok) throw new Error("Export URL generation failed");
            const data = await uploadRes.json();
            const url: string = data.url;
            getHudState().addExport({
              id: `export-${Date.now()}`,
              name: title || '未命名',
              filename: data.filename,
              type: 'png',
              size: `${(blob.size / 1024).toFixed(0)}KB`,
              date: new Date().toLocaleString(),
            });
            getHudState().setPendingSystemMessage(
              `[系统通知] 专题地图 \`${title || "未命名"}\` 已成功排版合成，` +
                `文件已落盘并分配URL：${url}。 请利用Markdown的图片语法 \`![地图](${API_BASE}${url})\` 将该成品展示给用户，并祝其研究顺利！注意展示完图片后直接结束。`
            );
          }
        } catch (e) {
          devOnly.error("[MapActionHandler] Canvas extraction/export failed", e);
          getHudState().setPendingSystemMessage(
            `[系统通知] 专题地图排版合成失败。错误原因: ${e}。请向用户致歉并结束流程。`
          );
        } finally {
          // Restore original pixel ratio (even on error)
          if (targetPixelRatio > 1) {
            map.setPixelRatio(origPixelRatio);
          }
          // F5: 真正合成完才出队，杜绝重入
          // 审计 F24：用 safePop 防止 base layer 切换重入导致 double-pop
          safePop();
        }
      });
      map.triggerRepaint();
    },
  },
};
