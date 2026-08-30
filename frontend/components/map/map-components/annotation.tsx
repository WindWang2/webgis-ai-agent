'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { isFloating, placementStyle, positionClass, resolveVariant, stackedTopStyle } from './helpers';
import type { RendererContext } from './types';
import {
  anchorFractionInBounds,
  validBounds,
  validLngLat,
  type GeoBounds,
} from '@/lib/map-components/geo-anchor';

/**
 * annotation 渲染器（v2 注记框架）：三种形态共享同一组件类型 ——
 * - text（缺省）：静态注释卡（旧行为不变）；
 * - callout：options.anchor=[lng, lat] 的地理锚定注释 —— 卡片定锚点、
 *   引线连接（live 用真实 map bounds 投影；bounds 缺席 → 降级为普通
 *   注释卡，绝不虚构位置）；
 * - group：options.items 多条相关注记（Top N / 震中 / 重要场所）——
 *   一条组件实例渲染一组，每条可带 anchor 成为组内 callout。
 *
 * 导出侧 drawChromeAnnotation 同链消费同一 options 语义（exporter 侧用
 * boundsFromCenterZoom 推导 bounds —— 同一投影函数，ADR-0081 parity）。
 */

const MAX_ITEMS = 12; // 与后端 MAX_ANNOTATION_ITEMS 同值（防御双层）
const MAX_LINES = 8;
const MAX_LINE_CHARS = 80;

interface AnnotationItem {
  text: string;
  anchor?: [number, number];
}

function parseItems(options: Record<string, unknown>): AnnotationItem[] | null {
  const raw = options['items'];
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const items: AnnotationItem[] = [];
  for (const entry of raw.slice(0, MAX_ITEMS)) {
    if (!entry || typeof entry !== 'object') continue;
    const rec = entry as Record<string, unknown>;
    const text = typeof rec['text'] === 'string' ? rec['text'] : '';
    if (!text.trim()) continue;
    const anchor = Array.isArray(rec['anchor']) && validLngLat(rec['anchor'] as [number, number])
      ? ([Number(rec['anchor'][0]), Number(rec['anchor'][1])] as [number, number])
      : undefined;
    items.push({ text, anchor });
  }
  return items.length ? items : null;
}

function parseAnchor(options: Record<string, unknown>): [number, number] | null {
  const raw = options['anchor'];
  return raw && validLngLat(raw as [number, number])
    ? ([Number((raw as [number, number])[0]), Number((raw as [number, number])[1])] as [number, number])
    : null;
}

function textLines(text: string): string[] {
  return text.split('\n').slice(0, MAX_LINES).map((l) => l.slice(0, MAX_LINE_CHARS));
}

/** 注释卡主体（callout 与静态卡同款视觉：左边线强调 + 弱文本）。 */
function AnnotationCard({ lines, maxWidth }: { lines: string[]; maxWidth?: number }) {
  return (
    <div
      className="map-chrome rounded-chrome border-l-2 border-l-map-chrome-ink px-2.5 py-1.5 text-caption leading-relaxed text-map-chrome-ink-muted"
      style={maxWidth ? { maxWidth } : undefined}
    >
      {lines.map((line, i) => (
        <div key={i}>{line}</div>
      ))}
    </div>
  );
}

/**
 * 地理锚定 callout：百分比定位（frac × 100%），不依赖视口像素尺寸 ——
 * live 与导出共享同一 frac 语义（导出按画布像素同式换算），卡片避让方向
 * 由 anchor 所在象限决定（确定性）。bounds 外夹取到边缘（指示仍在，不丢失）。
 */
function AnchoredCallout({
  anchor,
  lines,
  bounds,
}: {
  anchor: [number, number];
  lines: string[];
  bounds: GeoBounds;
}) {
  const frac = anchorFractionInBounds(anchor, bounds);
  if (!frac) return null;
  const fx = Math.min(1, Math.max(0, frac.fx));
  const fy = Math.min(1, Math.max(0, frac.fy));
  const leftPct = fx * 100;
  const topPct = (1 - fy) * 100;
  // 卡片避让：anchor 在视口右半 → 卡片向左偏；下半 → 向上偏（确定性象限规则）
  const flipX = fx > 0.6;
  const flipY = fy < 0.4;
  const cardTransform = `translate(${flipX ? 'calc(-100% - 26px)' : '26px'}, ${flipY ? 'calc(-100% - 10px)' : '10px'})`;
  // 引线终点：anchor 朝卡片方向的固定比例偏移（同一象限规则）
  const endFx = fx + (flipX ? -0.08 : 0.08);
  const endFy = (1 - fy) + (flipY ? 0.06 : -0.06);
  return (
    <div
      data-testid="spec-chrome-annotation-callout"
      className="pointer-events-none absolute inset-0 z-30"
    >
      <svg
        className="absolute inset-0 h-full w-full"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-hidden
      >
        <line
          x1={leftPct} y1={topPct}
          x2={endFx * 100} y2={endFy * 100}
          stroke="rgba(30,41,59,0.65)" strokeWidth={0.18}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div
        className="absolute h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#e11d48]"
        style={{ left: `${leftPct}%`, top: `${topPct}%` }}
      />
      <div
        className="map-chrome absolute rounded-chrome border-l-2 border-l-map-chrome-ink px-2.5 py-1.5 text-caption leading-relaxed text-map-chrome-ink-muted"
        style={{ left: `${leftPct}%`, top: `${topPct}%`, transform: cardTransform, maxWidth: 180 }}
      >
        {lines.map((line, i) => (
          <div key={i}>{line}</div>
        ))}
      </div>
    </div>
  );
}

function AnnotationRenderer(component: MapSpecComponent, ctx: RendererContext) {
  const options = (component.options ?? {}) as Record<string, unknown>;
  const variant = resolveVariant(component, 'text');
  const floating = isFloating(component);
  const items = parseItems(options);
  const anchor = items ? null : parseAnchor(options);
  const text = typeof options['text'] === 'string' ? options['text'] : '';
  const lines = items ? items.map((it) => it.text) : textLines(text);
  if (!lines.length) return null;

  // callout 形态：单条 + anchor + 有效 bounds → 地理锚定
  const bounds = validBounds(ctx.bounds ?? null) ? (ctx.bounds as GeoBounds) : null;
  const useCallout = (variant === 'callout' || variant === 'group') && bounds != null &&
    (items ? items.some((it) => it.anchor) : anchor != null);

  if (useCallout && bounds) {
    // group：带 anchor 的条目逐条锚定；无 anchor 条目并成一张静态卡挂在
    // 组件槽位。文本/锚定语义与导出 drawChromeAnnotation 同链。
    if (items) {
      const anchored = items.filter((it) => it.anchor);
      const plain = items.filter((it) => !it.anchor);
      return (
        <>
          {anchored.map((it, i) => (
            <AnchoredCallout
              key={i}
              anchor={it.anchor!}
              lines={textLines(it.text)}
              bounds={bounds}
            />
          ))}
          {plain.length > 0 && (
            <div
              data-testid="spec-chrome-annotation"
              className={`absolute z-30 max-w-[min(40ch,60%)] ${floating ? '' : positionClass(component)}`}
              style={floating ? placementStyle(component) : stackedTopStyle(component, ctx.topSlotIndexes)}
            >
              <AnnotationCard lines={plain.map((p) => p.text)} />
            </div>
          )}
        </>
      );
    }
    if (anchor) {
      return (
        <AnchoredCallout
          anchor={anchor}
          lines={lines}
          bounds={bounds}
        />
      );
    }
  }

  // 静态卡（text 形态 / bounds 缺席降级）—— 旧行为不变
  return (
    <div
      data-testid="spec-chrome-annotation"
      className={`map-chrome absolute z-30 max-w-[min(40ch,60%)] ${floating ? '' : positionClass(component)}`}
      style={floating ? placementStyle(component) : stackedTopStyle(component, ctx.topSlotIndexes)}
    >
      <AnnotationCard lines={lines} />
    </div>
  );
}

registerComponentRenderer('annotation', AnnotationRenderer);
