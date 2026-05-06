'use client';

import { useEffect, useRef } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { getThemeColors } from '@/lib/theme';

interface MapCanvasProps {
  children?: React.ReactNode;
  showGrid?: boolean;
}

export default function MapCanvas({ children, showGrid = true }: MapCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const theme = useHudStore((s) => s.theme);
  const colors = getThemeColors(theme);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Set canvas size
    canvas.width = 1440;
    canvas.height = 900;

    const W = canvas.width;
    const H = canvas.height;

    const isDark = theme === 'dark';

    // Background gradient
    const bg = ctx.createLinearGradient(0, 0, W, H);
    if (isDark) {
      bg.addColorStop(0, '#1e293b');
      bg.addColorStop(0.5, '#0f172a');
      bg.addColorStop(1, '#1e293b');
    } else {
      bg.addColorStop(0, '#d4e4f0');
      bg.addColorStop(0.5, '#dce8f2');
      bg.addColorStop(1, '#c8dae8');
    }
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);

    // Land masses
    const landColor = isDark ? 'rgba(30,64,175,0.3)' : 'rgba(167,210,180,0.35)';
    const landColorLight = isDark ? 'rgba(30,64,175,0.15)' : 'rgba(167,210,180,0.25)';
    ctx.fillStyle = landColor;
    roundRect(ctx, 0.05 * W, 0.1 * H, 0.18 * W, 0.28 * H, 12);
    ctx.fill();
    ctx.fillStyle = landColorLight;
    roundRect(ctx, 0.6 * W, 0.05 * H, 0.3 * W, 0.22 * H, 12);
    ctx.fill();
    ctx.fillStyle = landColorLight;
    roundRect(ctx, 0.2 * W, 0.55 * H, 0.25 * W, 0.35 * H, 12);
    ctx.fill();
    ctx.fillStyle = landColor;
    roundRect(ctx, 0.7 * W, 0.6 * H, 0.25 * W, 0.32 * H, 12);
    ctx.fill();

    // Grid lines
    ctx.strokeStyle = isDark ? 'rgba(148,163,184,0.25)' : 'rgba(255,255,255,0.5)';
    ctx.lineWidth = 1.8;
    for (let i = 0; i < 8; i++) {
      ctx.beginPath();
      ctx.moveTo(0, (i + 1) * H / 8);
      ctx.bezierCurveTo(W * 0.3, (i + 1) * H / 8 + Math.sin(i) * 20, W * 0.7, (i + 1) * H / 8 - Math.cos(i) * 15, W, (i + 1) * H / 8 + 10);
      ctx.stroke();
    }
    for (let i = 0; i < 10; i++) {
      ctx.beginPath();
      ctx.moveTo((i + 1) * W / 10, 0);
      ctx.bezierCurveTo((i + 1) * W / 10 + Math.sin(i) * 15, H * 0.3, (i + 1) * W / 10 - Math.cos(i) * 10, H * 0.7, (i + 1) * W / 10 + 5, H);
      ctx.stroke();
    }

    // Major lines
    ctx.strokeStyle = isDark ? 'rgba(148,163,184,0.5)' : 'rgba(255,255,255,0.82)';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(0, H * 0.42);
    ctx.lineTo(W, H * 0.42);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(W * 0.38, 0);
    ctx.lineTo(W * 0.38, H);
    ctx.stroke();

    // Lake
    ctx.fillStyle = isDark ? 'rgba(59,130,246,0.35)' : 'rgba(163,199,225,0.55)';
    ctx.beginPath();
    ctx.ellipse(W * 0.72, H * 0.32, W * 0.12, H * 0.09, 0.3, 0, Math.PI * 2);
    ctx.fill();

    // Fields
    const fields = [
      [0.41, 0.1, 0.08, 0.06], [0.51, 0.1, 0.07, 0.06],
      [0.41, 0.18, 0.08, 0.05], [0.51, 0.18, 0.07, 0.05],
      [0.08, 0.44, 0.08, 0.07], [0.18, 0.44, 0.07, 0.07],
      [0.28, 0.44, 0.08, 0.07], [0.08, 0.53, 0.08, 0.06],
      [0.18, 0.53, 0.07, 0.06], [0.28, 0.53, 0.08, 0.06],
    ];
    fields.forEach(([x, y, w, h]) => {
      ctx.fillStyle = isDark ? 'rgba(51,65,85,0.55)' : 'rgba(200,215,228,0.58)';
      ctx.fillRect(x * W, y * H, w * W, h * H);
      ctx.strokeStyle = isDark ? 'rgba(148,163,184,0.35)' : 'rgba(255,255,255,0.45)';
      ctx.lineWidth = 0.5;
      ctx.strokeRect(x * W, y * H, w * W, h * H);
    });
  }, [theme]);

  const isDark = theme === 'dark';
  return (
    <div style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}>
      <canvas
        ref={canvasRef}
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
      />
      {showGrid && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            mixBlendMode: 'multiply',
            opacity: 0.38,
            pointerEvents: 'none',
            backgroundColor: isDark ? '#1e293b' : '#dce8f2',
            backgroundImage: isDark
              ? 'linear-gradient(rgba(148,163,184,0.15) 1px, transparent 1px), linear-gradient(90deg, rgba(148,163,184,0.15) 1px, transparent 1px)'
              : 'linear-gradient(rgba(15,23,42,0.032) 1px, transparent 1px), linear-gradient(90deg, rgba(15,23,42,0.032) 1px, transparent 1px)',
            backgroundSize: '40px 40px',
            animation: 'mapGridMove 8s linear infinite',
          }}
        />
      )}
      {children}

      <style dangerouslySetInnerHTML={{ __html: `
        @keyframes mapGridMove {
          from { background-position: 0 0; }
          to { background-position: 40px 40px; }
        }
      `}} />
    </div>
  );
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}
