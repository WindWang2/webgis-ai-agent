import maplibregl from 'maplibre-gl';

/**
 * Captures the current map canvas and returns it as a Blob.
 * @param map The MapLibre map instance.
 * @returns A promise resolving to a PNG Blob.
 */
export async function captureMapCanvas(map: maplibregl.Map): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const canvas = map.getCanvas();
    // Using image/png by default for better quality
    canvas.toBlob((blob) => {
      if (blob) {
        resolve(blob);
      } else {
        reject(new Error('Failed to capture map canvas'));
      }
    }, 'image/png');
  });
}

export interface ExportOptions {
  paperSize?: 'screen' | 'A4';
  orientation?: 'landscape' | 'portrait';
  dpi?: number;
}

/**
 * Prepares a new canvas for export, handling cropping and high-DPI upscaling.
 */
export function prepareExportCanvas(
  sourceCanvas: HTMLCanvasElement,
  options: ExportOptions = {}
): { canvas: HTMLCanvasElement; scaleX: number; scaleY: number; srcX: number; srcY: number; srcW: number; srcH: number } {
  const { paperSize = 'screen', orientation = 'landscape', dpi = 96 } = options;
  
  let srcW = sourceCanvas.width;
  let srcH = sourceCanvas.height;
  let srcX = 0;
  let srcY = 0;

  // 1. Calculate Crop Box if A4
  if (paperSize === 'A4') {
    const targetRatio = orientation === 'landscape' ? 1.414 : 1 / 1.414;
    const canvasRatio = srcW / srcH;
    
    if (canvasRatio > targetRatio) {
      const newW = srcH * targetRatio;
      srcX = (srcW - newW) / 2;
      srcW = newW;
    } else {
      const newH = srcW / targetRatio;
      srcY = (srcH - newH) / 2;
      srcH = newH;
    }
  }

  // 2. High-DPI Upscaling calculation
  const dpiMultiplier = dpi / 96;
  const targetW = Math.round(srcW * dpiMultiplier);
  const targetH = Math.round(srcH * dpiMultiplier);

  const exportCanvas = document.createElement("canvas");
  exportCanvas.width = targetW;
  exportCanvas.height = targetH;
  const ctx = exportCanvas.getContext("2d");
  if (!ctx) throw new Error("Could not get canvas context");

  // Draw cropped base map
  ctx.drawImage(sourceCanvas, srcX, srcY, srcW, srcH, 0, 0, targetW, targetH);

  return {
    canvas: exportCanvas,
    scaleX: dpiMultiplier,
    scaleY: dpiMultiplier,
    srcX,
    srcY,
    srcW,
    srcH
  };
}

/**
 * Composes a professional map layout with title, subtitle, scale bar, and compass.
 * @param canvas The canvas element to draw on (containing the map image).
 * @param title The map title.
 * @param subtitle Optional subtitle.
 * @param options Configuration options (dpi, theme, showScale, showCompass, etc.)
 */
export function composeLayout(
  canvas: HTMLCanvasElement,
  title: string,
  subtitle?: string,
  options: any = {}
) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const {
    dpi = 96,
    theme = 'light',
    showScale = true,
    showCompass = true,
    showWatermark = true,
    showLegend = true,
    mapCenter,
    mapZoom,
    mapBearing = 0,
    thematicLayer,
  } = options;

  const dark_mode = theme === 'dark';
  const dpiMultiplier = dpi / 96;
  const scalePx = (val: number) => val * dpiMultiplier;
  const targetW = canvas.width;
  const targetH = canvas.height;

  // 1. Header gradient
  const headerH = subtitle ? scalePx(130) : scalePx(100);
  const headerGrad = ctx.createLinearGradient(0, 0, 0, headerH);
  headerGrad.addColorStop(0, dark_mode ? "rgba(0,10,20,0.88)" : "rgba(255,255,255,0.96)");
  headerGrad.addColorStop(0.65, dark_mode ? "rgba(0,10,20,0.45)" : "rgba(255,255,255,0.55)");
  headerGrad.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = headerGrad;
  ctx.fillRect(0, 0, targetW, headerH);

  // 2. Title
  ctx.fillStyle = dark_mode ? "#00f2ff" : "#1e293b";
  ctx.font = `bold ${scalePx(32)}px sans-serif`;
  ctx.fillText(title || "WebGIS AI Agent", scalePx(56), scalePx(52));

  if (subtitle) {
    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.72)" : "rgba(30,41,59,0.72)";
    ctx.font = `${scalePx(20)}px sans-serif`;
    ctx.fillText(subtitle, scalePx(56), scalePx(82));
  }

  // 3. Scale bar
  if (showScale && mapCenter && mapZoom !== undefined) {
    const metersPerPx =
      (156543.03392 * Math.cos((mapCenter.lat * Math.PI) / 180)) /
      Math.pow(2, mapZoom);
    
    const logicalW = targetW / dpiMultiplier;
    const targetPx = Math.round(logicalW * 0.12);
    const rawMeters = metersPerPx * targetPx;
    
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawMeters)));
    const nice = [1, 2, 5, 10].reduce((prev, n) => {
      const candidate = n * magnitude;
      return Math.abs(candidate - rawMeters) < Math.abs(prev - rawMeters)
        ? candidate
        : prev;
    }, magnitude);
    
    const barPx = (nice / metersPerPx) * dpiMultiplier;
    const barLabel = nice >= 1000 ? `${nice / 1000} km` : `${nice} m`;

    const bx = scalePx(56), by = targetH - scalePx(52), bh = scalePx(8);
    ctx.strokeStyle = dark_mode ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.8)";
    ctx.lineWidth = scalePx(1.5);
    ctx.strokeRect(bx, by, barPx, bh);
    
    const segCount = 4;
    const segW = barPx / segCount;
    for (let i = 0; i < segCount; i++) {
      ctx.fillStyle =
        i % 2 === 0
          ? dark_mode ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.8)"
          : "rgba(0,0,0,0)";
      ctx.fillRect(bx + i * segW, by, segW, bh);
    }
    
    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.95)" : "#1e293b";
    ctx.font = `bold ${scalePx(13)}px sans-serif`;
    ctx.textAlign = "left";
    ctx.fillText("0", bx, by - scalePx(4));
    ctx.textAlign = "right";
    ctx.fillText(barLabel, bx + barPx, by - scalePx(4));
    ctx.textAlign = "left";
  }

  // 4. Compass
  if (showCompass) {
    const bearing = mapBearing;
    const cx = targetW - scalePx(64), cy = scalePx(64), r = scalePx(28);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate((bearing * Math.PI) / 180);

    ctx.shadowColor = "rgba(0,0,0,0.4)";
    ctx.shadowBlur = scalePx(6);

    ctx.beginPath();
    ctx.moveTo(0, -r);
    ctx.lineTo(r * 0.35, 0);
    ctx.lineTo(0, r * 0.2);
    ctx.lineTo(-r * 0.35, 0);
    ctx.closePath();
    ctx.fillStyle = "#e53e3e";
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(0, r);
    ctx.lineTo(r * 0.35, 0);
    ctx.lineTo(0, r * 0.2);
    ctx.lineTo(-r * 0.35, 0);
    ctx.closePath();
    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.9)" : "#f8fafc";
    ctx.fill();

    ctx.shadowBlur = 0;
    ctx.beginPath();
    ctx.arc(0, 0, scalePx(4), 0, 2 * Math.PI);
    ctx.fillStyle = "#1e293b";
    ctx.fill();

    ctx.restore();

    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.95)" : "#1e293b";
    ctx.font = `bold ${scalePx(13)}px sans-serif`;
    ctx.textAlign = "center";
    ctx.fillText("N", cx, cy - r - scalePx(6));
    ctx.textAlign = "left";
  }

  // 5. Legend
  if (showLegend && thematicLayer) {
    // Treat thematicLayer as ThematicStyleDef
    const styleDef = thematicLayer as any;
    const field = styleDef.field || '未知字段';
    const colors = styleDef.colors || [];
    const legendLabels = styleDef.legend_labels || [];
    
    // Fallback if it's the old object structure (l.source.metadata)
    const meta = (styleDef.source as any)?.metadata;
    let actualField = field;
    let actualColors = colors;
    let actualLabels = legendLabels;
    
    if (meta && meta.breaks && meta.palette) {
      const COLOR_PALETTES: Record<string, string[]> = {
        YlOrRd: ["#ffffb2","#fed976","#feb24c","#fd8d3c","#f03b20","#bd0026"],
        Blues:  ["#eff3ff","#bdd7e7","#6baed6","#3182bd","#08519c"],
        Greens: ["#edf8e9","#bae4b3","#74c476","#31a354","#006d2c"],
        Reds:   ["#fee5d9","#fcae91","#fb6a4a","#de2d26","#a50f15"],
        Viridis:["#440154","#3b528b","#21908c","#5dc963","#fde725"],
        Magma:  ["#000004","#3b0f70","#8c2981","#de4968","#feb078","#fcfdbf"],
      };
      actualField = meta.field || actualField;
      actualColors = COLOR_PALETTES[meta.palette] ?? COLOR_PALETTES["YlOrRd"];
      
      const formatNum = (n: number) =>
        n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` :
        n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` :
        n.toFixed(1);
        
      actualLabels = [];
      for (let i = 0; i < meta.breaks.length - 1; i++) {
        actualLabels.push(`${formatNum(meta.breaks[i])} – ${formatNum(meta.breaks[i + 1])}`);
      }
    }

    if (actualColors.length > 0 && actualLabels.length > 0) {
      const classes = Math.min(actualColors.length, actualLabels.length);
      const itemH = scalePx(22), itemW = scalePx(18), padding = scalePx(10), gapX = scalePx(8);
      
      // Calculate max text width for legend labels
      ctx.font = `${scalePx(11)}px sans-serif`;
      let maxTextW = 0;
      for (const label of actualLabels) {
        maxTextW = Math.max(maxTextW, ctx.measureText(label).width);
      }
      
      const legendW = padding * 2 + itemW + gapX + maxTextW + scalePx(10);
      const legendH = padding * 2 + scalePx(24) + classes * itemH;
      const lx = targetW - legendW - scalePx(56);
      const ly = targetH - legendH - scalePx(56);

      ctx.fillStyle = dark_mode ? "rgba(0,10,20,0.82)" : "rgba(255,255,255,0.88)";
      ctx.beginPath();
      const rad = scalePx(8);
      ctx.moveTo(lx + rad, ly);
      ctx.lineTo(lx + legendW - rad, ly);
      ctx.arcTo(lx + legendW, ly, lx + legendW, ly + rad, rad);
      ctx.lineTo(lx + legendW, ly + legendH - rad);
      ctx.arcTo(lx + legendW, ly + legendH, lx + legendW - rad, ly + legendH, rad);
      ctx.lineTo(lx + rad, ly + legendH);
      ctx.arcTo(lx, ly + legendH, lx, ly + legendH - rad, rad);
      ctx.lineTo(lx, ly + rad);
      ctx.arcTo(lx, ly, lx + rad, ly, rad);
      ctx.closePath();
      ctx.fill();

      ctx.fillStyle = dark_mode ? "#00f2ff" : "#1e293b";
      ctx.font = `bold ${scalePx(12)}px sans-serif`;
      ctx.fillText(`字段: ${actualField}`, lx + padding, ly + padding + scalePx(12));

      for (let i = 0; i < classes; i++) {
        const iy = ly + padding + scalePx(24) + i * itemH;
        ctx.fillStyle = actualColors[i];
        ctx.fillRect(lx + padding, iy, itemW, itemH - scalePx(4));
        ctx.strokeStyle = "rgba(128,128,128,0.4)";
        ctx.lineWidth = scalePx(0.5);
        ctx.strokeRect(lx + padding, iy, itemW, itemH - scalePx(4));
        ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.85)" : "#334155";
        ctx.font = `${scalePx(11)}px sans-serif`;
        ctx.fillText(
          actualLabels[i],
          lx + padding + itemW + gapX,
          iy + itemH - scalePx(8)
        );
      }
    }
  }

  // 6. Watermark
  if (showWatermark) {
    ctx.fillStyle = dark_mode ? "rgba(255,255,255,0.5)" : "rgba(0,0,0,0.4)";
    ctx.textAlign = "right";
    ctx.font = `bold ${scalePx(16)}px monospace`;
    ctx.fillText("Generated by WebGIS AI Agent", targetW - scalePx(36), targetH - scalePx(18));
    ctx.textAlign = "left";
  }
}

/**
 * Triggers a file download for a Blob.
 * @param blob The Blob to download.
 * @param filename The name of the file.
 */
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
