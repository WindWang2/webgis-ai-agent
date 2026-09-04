'use client';
import React from 'react';
import type { MapSpecComponent } from '@/lib/mapspec-compiler/types';
import { registerComponentRenderer } from './registry';
import { resolveVariant } from './helpers';
import { FloatingChrome, usePlacementPatchedComponent } from './floating-chrome';
import type { RendererContext } from './types';

/**
 * methodology_note 渲染器（VNext §5 方法论诚实的产品面）：
 * options.warnings = [{code?, pattern?, text}]。稳定警告码 + 文案随 live
 * 地图渲染 —— 「缺分母不能谈公平性」长在产品上，不藏在日志里。
 * 防御式解析（坏载荷 → 空态卡片）；variant default | compact。
 */

interface MethodologyWarning {
  code?: string;
  pattern?: string;
  text: string;
}

function parseWarnings(raw: unknown): MethodologyWarning[] | null {
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const out: MethodologyWarning[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const rec = item as Record<string, unknown>;
    const text = rec['text'];
    if (typeof text !== 'string' || !text.trim()) continue;
    out.push({
      code: typeof rec['code'] === 'string' && rec['code'] ? (rec['code'] as string) : undefined,
      pattern: typeof rec['pattern'] === 'string' && rec['pattern'] ? (rec['pattern'] as string) : undefined,
      text,
    });
  }
  return out.length > 0 ? out : null;
}

function MethodologyNoteView({ component, ctx }: { component: MapSpecComponent; ctx?: RendererContext }) {
  const patched = usePlacementPatchedComponent(component);
  const variant = resolveVariant(patched, 'default') === 'compact' ? 'compact' : 'default';
  const warnings = parseWarnings(patched.options?.['warnings']);

  return (
    <FloatingChrome
      component={patched}
      title="方法论披露"
      topSlotIndexes={ctx?.topSlotIndexes}
      testId="spec-chrome-methodology-note"
      dataVariant={variant}
      bodyClassName={variant === 'compact' ? 'p-1.5' : 'p-2'}
    >
      {warnings ? (
        <ul className={`flex flex-col ${variant === 'compact' ? 'gap-0.5' : 'gap-1'}`} role="list">
          {warnings.map((w, i) => (
            <li
              key={`${w.code ?? w.pattern ?? 'warn'}#${i}`}
              data-code={w.code}
              className={`rounded-sm border-l-2 border-amber-500/80 px-1 ${
                variant === 'compact' ? 'py-0.5' : 'py-1'
              } text-map-chrome-ink`}
            >
              {w.code ? (
                <span className="mr-1 font-mono text-micro text-map-chrome-ink-muted">{w.code}</span>
              ) : null}
              <span className={variant === 'compact' ? 'text-caption' : 'text-caption leading-snug'}>
                {w.text}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <div
          className="flex min-h-12 items-center justify-center px-2 py-3 text-caption text-map-chrome-ink-muted"
          data-state="empty"
          role="status"
        >
          无方法论限制披露
        </div>
      )}
    </FloatingChrome>
  );
}

function MethodologyNoteRenderer(component: MapSpecComponent, _ctx: RendererContext) {
  return <MethodologyNoteView component={component} ctx={_ctx} />;
}

registerComponentRenderer('methodology_note', MethodologyNoteRenderer);
